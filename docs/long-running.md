# Довгий запуск після виходу з SSH

Щоб процес **не завершився** після закриття терміналу або розриву SSH, запускайте його у **фоні** (`nohup` і `&`) або в **`tmux`** / **`screen`**.

## `nohup`

Утиліта **`nohup`** входить до пакета **`coreutils`** (на Debian/Ubuntu зазвичай уже є як `/usr/bin/nohup`). Окремого пакета `apt install nohup` немає; якщо команди немає: `sudo apt install coreutils`.

Приклад з каталогу проєкту та активованим venv:

```bash
cd /шлях/до/Indexer
source .venv/bin/activate
nohup python indexer.py lutsk_marriages 'https://drive.google.com/drive/folders/FOLDER_ID?usp=drive_link' --description volyn_darts_marriages </dev/null >>nohup.out 2>&1 &
```

**Важливо:**

- **`&` в кінці рядка** — без нього процес іде на **передній план**: здаватиметься, що термінал «завис», хоча скрипт просто працює. Повідомлення *nohup: ignoring input and appending output to 'nohup.out'* означає, що інтерактивний ввід ігнорується — це нормально для `nohup`.
- **URL у лапках** — символ `?` у посиланні Google Drive інакше обрізає аргументи в shell.
- Після `&` з’явиться PID у вигляді `[1] 12345` і знову запрошення оболонки (`$`) — можна закривати SSH.

## Зупинити фоновий індексатор

Зупиняється **процес Python** (індексатор), а не «nohup» як абстракція:

```bash
pgrep -af indexer.py
kill PID           # спочатку так (SIGTERM)
kill -9 PID      # лише якщо процес не завершився (SIGKILL; ризик обірваної операції)
```

Якщо запускали в тій самій сесії з `&` і бачили номер фону `[1]`: `jobs -l`, потім `kill %1`.

## `tmux` (альтернатива)

Зручно, коли потрібна «жива» консоль і повторне підключення:

```bash
sudo apt install tmux    # за потреби
tmux new -s indexer
# усередині: cd …, source .venv/bin/activate, python indexer.py …
# від’єднатися, не зупиняючи сесію: Ctrl+B, потім D
# пізніше: tmux attach -t indexer
```
