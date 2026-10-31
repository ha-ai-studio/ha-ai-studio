# HA AI Studio

**Your studio for AI Actions in Home Assistant.**

Connect your Home Assistant automations to configurable actions through MQTT. HA AI Studio provides an extensible foundation for local AI processing, with a clear interface for setup and live monitoring.

- **Configure each Action:** choose its activity, input broker and request topic, then define where results are delivered.
- **Keep Home Assistant in control:** automations handle conditions and follow-up actions. Studio can schedule independent measurement Actions for virtual Devices.
- **Exchange structured data:** JSON request and response messages carry correlation IDs so your automations can match results to requests.
- **See what happens:** follow incoming messages, processing steps and outgoing responses in a live activity view.
- **Use your MQTT setup:** manage multiple brokers and optionally enable Home Assistant MQTT Discovery.
- **Keep configuration local:** settings and Action state remain in the app's persistent data directory.

Open the interface directly in Home Assistant through Ingress. No separate Home Assistant integration is required.

Experimental release · amd64 · MQTT v5 · MIT license
