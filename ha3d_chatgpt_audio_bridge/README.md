# HA3D ChatGPT Audio Bridge

Experimental Home Assistant add-on used by HA3D to route the microphone captured in the HA3D dashboard to a virtual PulseAudio microphone inside Home Assistant OS.

The intended test path is:

`iPad microphone -> HA3D -> Supervisor ingress WebSocket -> this add-on -> PulseAudio virtual source -> Chromium add-on -> ChatGPT Voice`

The add-on creates a mono 48 kHz signed-16-bit PulseAudio source named `ha3d_chatgpt_mic` by default. For the beta it can temporarily make this source the default input so a Chromium-hosted ChatGPT Voice session can use it without opening the ChatGPT app on the iPad.

This is an experimental bridge. It does not contain an OpenAI API key and does not run an OpenAI model locally. ChatGPT itself runs in a separate Chromium add-on logged into chatgpt.com.
