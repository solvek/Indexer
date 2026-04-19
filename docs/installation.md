# Встановлення

## 1. Python

Потрібен Python 3.11+. Перевірити версію:

```bash
python --version
```

## 2. Залежності

На **Debian / Ubuntu** системний `pip` часто блокується (PEP 668, *externally-managed-environment*). Тоді ставте залежності **лише у venv**, не через `sudo pip` і не з `--break-system-packages`.

**Рекомендовано** (Linux, macOS, сервер):

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Якщо немає модуля `venv`: `sudo apt install python3-venv python3-full`.

Без venv (лише якщо середовище це дозволяє):

```bash
pip install -r requirements.txt
```

## 3. Ключі

```bash
cp sample.env .env
# відкрити .env і заповнити своїми ключами
```

| Змінна | Де взяти |
|---|---|
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `GOOGLE_DRIVE_API_KEY` | [console.cloud.google.com](https://console.cloud.google.com) → APIs & Services → Credentials → API key. Увімкніть Google Drive API. Для публічних папок. |
| `GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS` | (опційно) Абсолютний шлях до JSON **Desktop** OAuth client, якщо API key у проєкті блокують методи `files.get` / `files.list` (403 *method … blocked*). Перший запуск відкриє вхід Google у браузері. |
| `GOOGLE_DRIVE_OAUTH_TOKEN` | (опційно) Файл збереження access token, напр. `data/drive_oauth_token.json` (створюється автоматично; не комітайте). |
| `GOOGLE_DRIVE_SERVICE_ACCOUNT` | (рекоменд. для сервера) Абсолютний шлях до JSON **service account** (ключ, без пароля). **Браузер не потрібен** — зручно на Oracle Cloud та ін. Див. розділ нижче. Якщо змінна задана, індексатор **не** використає `GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS` / `GOOGLE_DRIVE_API_KEY` для Drive. |
| `DEFAULT_MODEL` | Модель за замовчуванням, можна не міняти |

## Google Drive: Service account (сервер / Oracle Cloud, без браузера)

1. **APIs & Services** → **Credentials** → **Create credentials** → **Service account** (назва довільна) → **Done** → зайдіть в обліковий запис SA → **Keys** → **Add key** → **Create new key** — формат **JSON** (файл з `client_email` та `private_key`).
2. **Спільний доступ** у [Google Drive](https://drive.google.com): на папку (або батьківську) зі сканами **додайте** e-mail service account, показаний у JSON у полі **`client_email`**, з роллю **Переглядач** (read-only; для індексації цього достатньо).
3. У **`.env` на VM** (шлях до ключа, який ви скопіювали/змонтували безпечно):
   ```env
   GOOGLE_DRIVE_SERVICE_ACCOUNT=/шлях/на/сервері/keys/drive-reader-sa.json
   ```
4. **Не** комітьте JSON ключа. На Oracle: покладіть файл в `$HOME/…`, secrets manager або `chmod 600` і **не** у публічні директорії.
5. Якщо одночасно задати `GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS` і `GOOGLE_DRIVE_SERVICE_ACCOUNT` — **використовується** service account (для оркестраторів: лишіть тільки SA в `.env` з деплойменту, щоб не плутати).

## Google Drive: OAuth (якщо вистачить API key або 403 на завантаження `alt=media`)

У **тому самому** Google Cloud проєкті, де вже увімкнено **Google Drive API**:

1. **OAuth consent screen** (меню **APIs & Services** → **OAuth consent screen**):
   - **User type**: зазвичай **External** (для особистого зберігання).
   - Заповніть обов’язкові поля (назва застосунку, e-mail власника), збережіть.
   - Поки застосунок **Testing** — у **Test users** **додайте свій Google-акаунт**, яким зайдете в Drive (той самий, з якого відкривається папка). Інакше Google покаже «this app is blocked».
2. **OAuth client ID** (**APIs & Services** → **Credentials** → **Create credentials** → **OAuth client ID**):
   - **Application type**: **Desktop** (як “Desktop app”).
   - Створіть, потім **Download JSON** (файл вигляду `client_secret_…json`). Зберігайте **поза** репозиторієм.
3. У **`.env`** (абсолютний шлях — надійніше):
   ```env
   GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS=/home/ви/Проекти/secret/client_secret_xxx.json
   GOOGLE_DRIVE_OAUTH_TOKEN=/home/ви/Projects/Indexer/data/drive_oauth_token.json
   ```
   Якщо задано `GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS`, **індексатор використає OAuth**, а не `GOOGLE_DRIVE_API_KEY` (ключ можна залишити для тесту або прибрати).
4. **Перший запуск** `indexer` зі вказівкою на **Google Drive URL** — відкриється **браузер**, увійдіть тим **test user**-акаунтом, дозвольте доступ. Файл токена створиться на `GOOGLE_DRIVE_OAUTH_TOKEN`, наступні запуски без браузера, доки дійсний **refresh** token.
5. Репозиторій: додайте `data/*oauth*.json` і `client_secret_*.json` в `.gitignore`, якщо це ще не зроблено.

**Важливо:** акаунт у веб-логіні **має мати** доступ у Drive до цієї папки (роль переглядача достатньо для `drive.readonly`).
