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

## Допоміжний скрип

```bash
sudo nano idxinit.sh
```

```bash
#!/bin/bash
cd indexer
python3 -m venv .venv
source .venv/bin/activate
```

Ctr+X and Save

```bash
sudo chmod +x idxinit.sh
```

# Запуск

```bash
. idxinit.sh
```

## Приклади команд

### Володимир народження

```bash
nohup python indexer.py volodymyr_births "https://drive.google.com/drive/folders/1N4dVipE7KV2-9F6Hqgn6y2bkiiLRdrxx?usp=drive_link" --extended-prompt volyn_darts_births --request-delay 150 </dev/null >>nohup.out 2>&1 &
```

### Володимир шлюби

```bash
nohup python indexer.py volodymyr_marriages "https://drive.google.com/drive/folders/1-MZ0j5yWqbILq4ldEIjMww5LiXLbsUmk?usp=drive_link" --extended-prompt volyn_darts_marriages --request-delay 150 </dev/null >>nohup.out 2>&1 &
```

### Закерзонці Волині

```bash
nohup python indexer.py zakerzonia_volodymyr "https://drive.google.com/drive/folders/1QiKWCUjOM1pxq08EmuNusO6Rs1-1uX1g?usp=drive_link" --extended-prompt zakerzonia --request-delay 150 </dev/null >>nohup.out 2>&1 &
```

## Вибірка даних

```sql
SELECT 
	s.folder,
	s.number,
	p.surname,
	p.name,
	json_extract(p.meta, '$.father') AS father,
	json_extract(p.meta, '$.yob') AS yob,
	json_extract(s2.meta, '$.zakerzonia') AS zakerzonia,
    p.meta,
	s.file
FROM persons p 
JOIN scans s ON s.id = p.scan_id
JOIN scans s2 ON s.number+1=s2.number
WHERE json_extract(s2.meta, '$.zakerzonia') IS NOT NULL
ORDER BY replace(replace(replace(replace(
    upper(p.surname),
    'Ґ', 'Г' || char(1)),
    'Є', 'Е' || char(1)),
    'І', 'И' || char(1)),
    'Ї', 'И' || char(2))
```

```bash
nohup python indexer.py lutsk_births "https://drive.google.com/drive/folders/1GgpSb00oPB51iA64T-R9kAQErspjADqT?usp=drive_link" --extended-prompt volyn_darts_births --request-delay 150 </dev/null >>nohup.out 2>&1 &
```

```bash
nohup python indexer.py volodymyr_raion_births "https://drive.google.com/drive/folders/1ze-EG4xc06ogWMDnsJzQW8bnUK_3-u2b?usp=drive_link" --extended-prompt volyn_darts_births --request-delay 150 </dev/null >>nohup.out 2>&1 &
```