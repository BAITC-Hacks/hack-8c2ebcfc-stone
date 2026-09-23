# Stone — HackAlem AI

## Описание решения и назначение

## Архитектура

## Используемые технологии

## Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Запуск

```bash
uvicorn backend.main:app --reload
```

После запуска API будет доступен по адресу:

- http://127.0.0.1:8000
- http://127.0.0.1:8000/docs

## Зависимости

Основные библиотеки:

- `fastapi` и `uvicorn` — backend API
- `python-dotenv` — переменные окружения из `.env`
- `chromadb` и `sentence-transformers` — локальный векторный поиск
- `openai` и `anthropic` — будущие AI-интеграции
- `pandas`, `numpy`, `plotly`, `streamlit` — аналитика и демо-интерфейс

## Переменные окружения

Создайте `.env` на основе `.env.example`:

```bash
cp .env.example .env
```

Переменные:

- `OPENAI_API_KEY` — ключ OpenAI, если используется OpenAI API
- `ANTHROPIC_API_KEY` — ключ Anthropic, если используется Anthropic API
- `DATABASE_PATH` — путь к локальной SQLite базе
- `CHROMA_PATH` — путь к локальному ChromaDB хранилищу

## Порядок проверки основного сценария

```bash
python -m compileall backend
uvicorn backend.main:app --reload
```

Минимальная проверка:

```bash
curl http://127.0.0.1:8000/health
```
