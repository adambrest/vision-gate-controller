# Gate Vision — Home Assistant App Repository

This repository distributes **Gate Vision**, a camera-verified controller for a
Home Assistant gate. It publishes `cover.main_gate` through MQTT Discovery and
provides an Ingress dashboard for status and control.

[![Open your Home Assistant instance and add this app repository.](https://my.home-assistant.io/badges/supervisor_store.svg)](https://my.home-assistant.io/redirect/supervisor_store/?repository_url=https%3A%2F%2Fgithub.com%2Fadambrest%2Fhome-assistant-gate-vision)

## Install

1. Install and start the official **Mosquitto broker** app, then configure the
   Home Assistant MQTT integration.
2. Click the button above, or open **Settings → Apps → App store**, open the
   repository menu, and add:

   ```text
   https://github.com/adambrest/home-assistant-gate-vision
   ```

3. Find **Gate Vision** in the app store and select **Install**.
4. Review its configuration, start it, and enable **Show in sidebar** if wanted.
5. Wait for three valid camera confirmations. MQTT Discovery will then create
   `cover.main_gate` with device class `gate`.

See the [Gate Vision documentation](./gate_vision/DOCS.md) for prerequisites,
configuration, operation, and safety behavior.

## Apps

### [Gate Vision](./gate_vision)

![Supports aarch64 architecture](https://img.shields.io/badge/aarch64-yes-green.svg)

The published container image is built automatically with Home Assistant's
official builder actions whenever `main` changes.
