# HA AI Studio

**Extend Home Assistant with AI Actions and plugins.**

Install plugins, configure Actions for your setup, and use their results in Home Assistant dashboards and automations. Each plugin supplies its processing logic and settings; Studio handles configuration, execution and the connection to Home Assistant.

- **Add AI capabilities:** install Action types from GitHub Releases or Git repositories, such as camera-based meter recognition with the [AI-on-the-Edge Meters plugin](https://github.com/ha-ai-studio/ha-ai-studio-actions-ai-on-the-edge-meters).
- **Run AI locally:** plugins with local models process data on your Studio server, without an external AI service. Check each plugin’s documentation for any external services it uses.
- **Configure your Actions:** use plugin settings and custom editors to adapt each instance to your inputs.
- **Use the results:** call Actions over MQTT or publish compatible readings and controls as Home Assistant sensors and buttons.
- **Schedule measurements:** combine Actions in virtual Devices and run them regularly or on demand.
- **Extend Studio:** build your own plugins with Python processing logic, settings and optional custom editors.

Home Assistant handles history, conditions, notifications and follow-up automations. Open Studio directly through Home Assistant's web interface.

See the [getting-started guide](https://github.com/ha-ai-studio/ha-ai-studio#readme) for installation and setup.

Experimental release · amd64 · MQTT v5 · MIT license
