# Основна інформація

## Підключитись до Oracle Cloud

```bash
ssh oracle
```

## Зайти в каталог

```bash
cd indexer
```

## Оновити скрипт

```bash
git pull
```

## Створити віртуальне середовище

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## Запуск скрипта

```bash
nohup python indexer.py volodymyr_births "https://drive.google.com/drive/folders/1IC43A3HaSn-FluEl88PFb9YOSYuYdRVf?usp=drive_link" --extended-prompt volyn_darts_births --request-delay 20 </dev/null >>nohup.out 2>&1 &
```

## Перегляд логів

```bash
tail -f indexer.log
```

# Ресурси

## [Рейти Gemini API](https://aistudio.google.com/rate-limit?timeRange=last-28-days&project=gen-lang-client-0102416739)

## [Oracle Cloud instance](https://cloud.oracle.com/compute/instances/ocid1.instance.oc1.ca-toronto-1.an2g6ljr3dzwxxicj46bqx5wo6fka5d3qjvegfzmy563o2oiipeycckn4eka?region=ca-toronto-1)


# Додаткові операції

## Якщо оновились залежності

```bash
pip install -r requirements.txt
```

## Активні процеси nohub

```bash
pgrep -af indexer
```

## Прибити процес

```bash
kill -9 PID
```

