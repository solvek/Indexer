# [Рейти Gemini API](https://aistudio.google.com/rate-limit?timeRange=last-28-days&project=gen-lang-client-0102416739)

# [Oracle Cloud instance](https://cloud.oracle.com/compute/instances/ocid1.instance.oc1.ca-toronto-1.an2g6ljr3dzwxxicj46bqx5wo6fka5d3qjvegfzmy563o2oiipeycckn4eka?region=ca-toronto-1)

# Підключитись до Oracle Cloud

```bash
ssh oracle
```

# Зайти в каталог

```bash
cd indexer
```

# Оновити скрипт

```bash
git pull
```

# Створити віртуальне середовище

```bash
python3 -m venv .venv
source .venv/bin/activate
```

# Якщо оновились залежності

```bash
pip install -r requirements.txt
```

# Запуск скрипта

```bash
nohup python indexer.py lutsk_marriages 'https://drive.google.com/drive/folders/1IC43A3HaSn-FluEl88PFb9YOSYuYdRVf?usp=drive_link' --description volyn_darts_marriages --request-delay 4.0</dev/null >>nohup.out 2>&1 &
```

# Перегляд логів

```bash
tail -f indexer.log
```

# Відправка оновлених баз в GitHub

```bash
git commit -m "databases update"
git push
```

Тоді ввести логін (емайл від GitHub) і в якості пароля ввести [Personal access token](https://github.com/settings/personal-access-tokens) з GitHub

# Вибрати всі записи з бази

```sql
SELECT s.folder, s.number, p.surname, p.name, p.father, p.yob, p.location, s.file
FROM persons p JOIN scans s ON s.id = p.scan_id
ORDER BY p.surname
```

# Активні процеси nohub

```bash
pgrep -af indexer
```

# Прибити процес

```bash
kill -9 PID
```