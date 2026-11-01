# HA AI Studio

**Extend Home Assistant with AI Actions and plugins.**

HA AI Studio brings plugin-provided AI capabilities into your smart home. Install a plugin, configure an Action for your setup, and use its results in Home Assistant dashboards and automations. Studio handles configuration, execution and the connection to Home Assistant; each plugin supplies the processing logic and its settings editor.

With the [AI-on-the-Edge Meters plugin](https://github.com/ha-ai-studio/ha-ai-studio-actions-ai-on-the-edge-meters), for example, a camera becomes a meter reader: local image recognition turns mechanical digits and dials into readings you can track in Home Assistant. The same plugin includes a separate history-based leak check.

## What Studio brings to your smart home

- **Add capabilities through plugins.** Install Action types from GitHub Releases or Git repositories and manage their versions in Studio.
- **Run AI locally.** Actions execute on your Studio server. Plugins with local models, such as the meter reader, process images there without sending them to an external AI service. Each Action has its own Python environment for its dependencies.
- **Make an Action fit your setup.** Create configured instances with their own settings. Plugins can provide dedicated editors, such as visual frame selection for meter recognition.
- **Use AI results in Home Assistant.** Call Actions from automations over MQTT, or expose their readings and controls as sensors and buttons through virtual Devices.
- **Run measurements regularly or on demand.** Schedule Device measurements or invoke a selected Action with a button. Home Assistant handles conditions, notifications and follow-up automations.
- **Build and share your own plugins.** Package Python processing logic, settings, an optional custom editor and reusable Device templates with the [plugin contract](ha_ai_studio/app/action_types/README.md).

A **plugin** supplies Action types. An **Action** is an instance configured for your camera, sensor or other input. A **Device** makes compatible Actions available as Home Assistant sensors and buttons. Plugins can combine AI-based recognition with conventional processing; available capabilities depend on the plugins you install. Local processing depends on the plugin: check its documentation for any external services it uses.

## Before you start

- **Home Assistant with Supervisor on amd64**, such as an amd64 Home Assistant OS installation. Studio is currently marked experimental.
- An **MQTT v5 broker** connected to Home Assistant's MQTT integration for publishing Devices or exchanging Action requests.
- Any inputs required by your chosen plugin, such as a Home Assistant camera entity for meter readings.

## 1. Install HA AI Studio

1. In Home Assistant, open **Settings → Apps → Install app → ⋮ → Repositories**.
2. Add this repository:

   ```text
   https://github.com/ha-ai-studio/ha-ai-studio
   ```

3. Install **HA AI Studio**, start it and open its web interface through Home Assistant.
4. Open Studio **Settings**. If the Home Assistant MQTT broker was discovered automatically, use it. Otherwise, add your broker and test the connection.

## 2. Add an Action plugin

Open **Settings → Action repositories**, add a source and wait for its catalog to load.

| Repository | Source type | Use it for |
| --- | --- | --- |
| [AI-on-the-Edge Meters](https://github.com/ha-ai-studio/ha-ai-studio-actions-ai-on-the-edge-meters) | **GitHub Release**, version `latest` | Camera-based meter readings, serial numbers, water-meter Device templates and optional leak detection. |
| [Actions Core](https://github.com/ha-ai-studio/ha-ai-studio-actions-core) | **Git**, ref `main` | Echo, a simple Action that returns the input data for testing MQTT connections. |

Use the repository URL from the link when adding the source. Install only plugins you trust: Actions run code on the Studio server, and custom editors run in your browser.

## 3. Configure an Action

1. Open **Actions → Create action**, enter a name and choose a type from an installed plugin.
2. Open the Action and wait for its environment to become **Ready**.
3. Configure its type-specific settings. For the meter reader, select a camera, capture a reference image and define the reading frames. Follow the [meter reader's setup guide](https://github.com/ha-ai-studio/ha-ai-studio-actions-ai-on-the-edge-meters#2-add-a-reference-image).
4. Test with the plugin's available controls, then **Save**.

For Device measurements, the Action must support an empty input and provide named readings. Its MQTT **Active** checkbox does not need to be enabled for Device use.

To call an Action directly over MQTT instead, configure **Input** with a broker and request topic, and **Output** with a result topic. Select **Active** and save. **Message schemas** describes the payloads; **Test in Home Assistant** provides a copyable test automation.

## 4. Turn Action results into a Device

Use a Device when you want to expose Action readings as sensors, schedule measurements or provide buttons in Home Assistant. Open the **Devices** page to begin.

1. Select **Create Device**, enter a name and optionally choose a plugin-provided Device template.
2. Assign the template's Actions, or use **Add sensor** to select an Action and one of its named readings.
3. Set each sensor's unit. For a cumulative water reading, use `m³`, enable the cumulative counter option and set the Home Assistant device class to `water`.
4. Set the measurement interval. Add a function if you want a button such as **Read Now**: give it a name and select the Action it should execute.
5. Select **Active** and **Save**. Use **Read now** to check the saved configuration immediately.

A Device can combine readings from several Actions. Each measurement invokes the Actions needed by its sensors. A configured function runs only its selected Action and updates that Action's sensors without resetting the measurement schedule.

To derive flow rate from a cumulative reading, add a **Rate of change** sensor. For a source in m³, choose factor **1000**, time base **Per minute** and unit **L/min**. The first reading establishes a baseline; subsequent readings produce the average flow over the interval.

## 5. Publish the Device to Home Assistant

1. In Studio **Settings**, edit the intended broker and enable **MQTT Discovery**. Use the broker connected to Home Assistant.
2. On the Device, enable **Publish this device to Home Assistant** and save.
3. Open Home Assistant's **MQTT integration** to find the Device and add its entities to a dashboard.

Publication includes the Device's sensors, configured function buttons and diagnostic states. You can use those entities in Home Assistant automations just like other sensors and buttons.

## Update Actions

Studio checks Git and GitHub Release sources for updates automatically. You can also use **Check for updates** in **Settings → Action repositories**.

**Update** downloads a new version into the catalog; existing Actions keep their selected version. To change an instance:

1. Save or discard its edits, deactivate Devices using it, and deactivate the Action's MQTT route if enabled. Wait for running work to finish.
2. In the Action's **General** card, choose **Version → Apply version**.
3. Wait for **Ready**, review the configuration and reactivate the Devices or MQTT route you use.

**Update all actions** previews a repository-wide update before applying it. See the [version reference](docs/DEVICES.md#action-repository-versions) for requirements and recovery behavior.

## Reuse a Device configuration

Choose **Export template** in the Devices table and **Import template** on the target installation. Assign compatible Actions and review the sensor reading IDs before activation.

Templates include sensor definitions, functions and the measurement interval. They exclude Action settings, credentials, images and measured values. Imported Devices start inactive and unpublished.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| No Action types are available | Add an Action repository in Settings and check its status. |
| Action environment is not Ready | Inspect its setup error and verify the selected plugin version and dependencies. |
| Device does not measure | Check the Device's saved Active state, Action readiness and reading assignments. |
| Sensor is unavailable | Inspect the Device's measurement log and the sensor's Status Message in Home Assistant. |
| Rate sensor has no value | It needs two valid numeric source readings taken at different times. |
| Device is missing from Home Assistant | Check the broker, MQTT Discovery and the Device's publication setting. |
| MQTT request gets no response | Check the Action's Active state and Input/Output topics. Use QoS 1 and retain disabled; inspect **Live activity**. For a retry, reuse the same request ID and payload while its result is cached. |

## Documentation

- [Device and Action reference](docs/DEVICES.md): sensor behavior, scheduling, functions, templates and versions.
- [MQTT guide and automation examples](ha_ai_studio/DOCS.md): request formats, results and Home Assistant test automations.
- [Plugin contract](ha_ai_studio/app/action_types/README.md): build your own Actions, editors and Device templates.
- [Local development](docs/DEVELOPMENT.md), [architecture](docs/ARCHITECTURE.md) and [release instructions](docs/RELEASING.md).
- [Changelog](ha_ai_studio/CHANGELOG.md).

## License

MIT — see [LICENSE](LICENSE). Action plugins have their own licenses; check their repositories. Third-party notices for Studio are listed in [THIRD_PARTY.md](ha_ai_studio/THIRD_PARTY.md).
