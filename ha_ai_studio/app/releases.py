"""Bounded public GitHub release downloads; never execute build scripts."""
import hashlib
import io
import json
from pathlib import PurePosixPath
import re
import stat
from urllib.parse import urlsplit, quote
import zipfile
import aiohttp
from .domain import Invalid

ASSET = 'ha-ai-studio-actions.zip'
LIMIT = 32 * 1024 * 1024


def repository_url(value):
    if not isinstance(value, str):
        raise Invalid('Provide a public GitHub repository URL.')
    match = re.fullmatch(r'https://github.com/([A-Za-z0-9_-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?', value)
    if not match or match[2] in ('.', '..'):
        raise Invalid('Provide a public GitHub repository URL.')
    return f'https://github.com/{match[1]}/{match[2]}'


async def download(session, url, limit):
    # Validate each redirect before following it. No credentials, arbitrary hosts or HTTP.
    for _ in range(6):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname not in
                {'api.github.com', 'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise Invalid('Invalid release download destination.')
        async with session.get(url, allow_redirects=False) as response:
            if response.status in (301, 302, 303, 307, 308):
                from urllib.parse import urljoin
                url = urljoin(url, response.headers['Location'])
                continue
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > limit:
                    raise Invalid('Release download exceeds its size limit.')
            return bytes(data)
    raise Invalid('Too many release download redirects.')


def unpack(data, target):
    total = 0
    seen = set()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 2048:
            raise Invalid('Release contains too many files.')
        for entry in entries:
            path = PurePosixPath(entry.filename)
            mode = entry.external_attr >> 16
            if (not path.parts or path.is_absolute() or '\\' in entry.filename
                    or any(p in ('', '.', '..') or p.startswith('.') for p in entry.filename.rstrip('/').split('/'))
                    or path.parts[0] not in ('actions', 'device_templates') or len(path.parts) < 2
                    or path.as_posix() in seen or entry.flag_bits & 1
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise Invalid('Invalid release archive path or file type.')
            seen.add(path.as_posix())
            total += entry.file_size
            if entry.file_size > 5 * 1024 * 1024 or total > LIMIT:
                raise Invalid('Release exceeds its extracted size limits.')
            destination = target.joinpath(*path.parts)
            if entry.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(entry))


async def fetch_release(url, ref, target=None):
    repository = repository_url(url).removeprefix('https://github.com/')
    endpoint = 'latest' if ref == 'latest' else 'tags/' + quote(ref, safe='')
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120), trust_env=False,
                                     headers={'User-Agent': 'HA-AI-Studio'}) as session:
        release = json.loads(await download(session, f'https://api.github.com/repos/{repository}/releases/{endpoint}', 1024 * 1024))
        if release.get('draft') is not False or release.get('prerelease') is not False:
            raise Invalid('Only published stable releases are supported.')
        tag = release.get('tag_name')
        if not isinstance(tag, str) or not tag or len(tag) > 128 or (ref != 'latest' and tag != ref):
            raise Invalid('Invalid release version.')
        assets = release.get('assets', [])
        def asset(name):
            matches = [a for a in assets if a.get('name') == name and a.get('state') == 'uploaded']
            if len(matches) != 1:
                raise Invalid('Release package or checksum is missing or ambiguous.')
            return matches[0]['browser_download_url']
        checksum = (await download(session, asset(ASSET + '.sha256'), 256)).decode('ascii')
        match = re.fullmatch(r'([a-f0-9]{64})  ' + re.escape(ASSET) + r'\n?', checksum)
        if not match:
            raise Invalid('Invalid release checksum.')
        if target is None:
            return {'tag': tag, 'release_id': release['id'], 'sha256': match[1]}
        data = await download(session, asset(ASSET), LIMIT)
    digest = hashlib.sha256(data).hexdigest()
    if digest != match[1]:
        raise Invalid('Release checksum mismatch.')
    unpack(data, target)
    return {'tag': tag, 'release_id': release['id'], 'sha256': digest}
