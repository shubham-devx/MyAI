# Model configuration

The backend selects its local Ollama model through `OLLAMA_MODEL` and the Ollama server through `OLLAMA_BASE_URL`. [local-model.example.json](local-model.example.json) shows the corresponding local model profile. `backend/app/providers/base.py` defines the async streaming contract; add future local or hosted providers by implementing `ModelProvider`, then select one through backend configuration. Model weights stay outside this repository.
