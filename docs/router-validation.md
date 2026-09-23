# Проверка изменений маршрутизатора

Ветка: `codex/router-intent-quality`. База: `70aacd6`.

Изменения устраняют противоречие single/multi-intent, добавляют примеры RU/KK
из каталога (не из dev-разметки), различия SC22/SC40, SC21/SC10 и SC18/SC14,
контекст последних реплик и описание слотов. Срочные сценарии ставятся первыми,
остальные сохраняют порядок; дубликаты удаляются.

## Локальная проверка без ключа

Python 3.10+ (проверено на 3.11), зависимости `openai==3.18.0`, `python-dotenv==1.2.3`.

```bash
python -m unittest discover -s tests -v
python -m compileall -q backend predictions.py tests
```

Тесты проверяют контракт вызова, сохранение нескольких намерений, порядок urgent,
дубликаты и недопустимые ответы. Они не измеряют качество LLM.

## Проверка качества перед merge

Добавить `OPENAI_API_KEY` в локальный `.env`. Не коммитить ключ или `.env`.
Для сравнения использовать одну модель (`ROUTER_MODEL`, по умолчанию `gpt-4o-mini`).
Сначала выполнить baseline на `70aacd6` в отдельном checkout, затем текущую ветку.

```bash
python predictions.py
python data/case_2/voice_router_dataset/evaluate.py predictions.json data/case_2/voice_router_dataset/dev_utterances.json
```

В текущей ветке `predictions.py --output /tmp/stone-router-candidate.json` позволяет
сохранить результат отдельно. Передать тот же путь в `evaluate.py`.

Сравнить primary accuracy, full match, multi-intent recall и ошибки по языкам.
Дополнительно проверить новые перефразировки пограничных запросов и продолжение
диалога с заполнением слотов. Dev-набор не заменяет скрытую оценку.

Исходные 92% full match / 94% primary accuracy взяты из TEAM_GUIDE.md;
в этой работе пока не воспроизведены: локально отсутствует API-ключ.
Увеличенный контекст может повысить задержку и стоимость вызова; это также нужно
измерить при живом прогоне. До этой проверки PR остаётся черновиком.
