# DataAlchemy

DataAlchemy is a small Gradio app for generating realistic synthetic datasets. Describe the dataset you want, choose a complexity, format, and row count, and the app designs related tables with a local Ollama model before synthesizing the actual rows with Faker.

## Tech

- Python 3.14
- [Gradio](https://gradio.app/) for the app UI
- [Ollama](https://ollama.com/) through its OpenAI-compatible API for local model calls
- [Faker](https://faker.readthedocs.io/) for coherent names, addresses, dates, and other generated values
- CSV, JSON, and Markdown export bundled as a ZIP download

## Run

```sh
cp env.example .env
uv run python main.py
```

After it starts, open `http://127.0.0.1:7860/`.

Set `OLLAMA_HOST` in `.env` if Ollama is not running at `http://127.0.0.1:11434`. Install at least one Ollama chat model before starting the app; DataAlchemy uses `gpt-oss:20b` when it is available, otherwise it selects the first local model.

## Notebook

Open `main.ipynb` if you prefer running the app cell by cell.
