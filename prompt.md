# Prompt

We need to expand oli_bot/backends/openai.py to support both CHAT COMPLETIONS (as it currently does already), and RESPONSES APIS

We can manage which one we'll be using from the SDK with an internal property to the `OpenAIBackend` class called responses_enabled that is configurable in config.py and the env var `OLI_OPENAI_RESPONSES_ENABLED`

The flag can be checked within `OpenAIBackend.generate()` and `OpenAIBackend.stream_generate()` to determine which sdk call to use -- we do NOT want to break the implemented `ModelBackend` interface (see `oli_bot/backends/base.py`)
