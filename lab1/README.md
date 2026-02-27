# Лабораторная работа №1 — Инструкция по запуску

## Структура проекта

```
lab1/
├── server.py        # TCP сервер
├── client.py        # TCP клиент
├── server_files/    # файлы на сервере (создаётся автоматически)
├── sessions/        # данные сессий для докачки (создаётся автоматически)
└── downloads/       # скачанные файлы на клиенте (создаётся автоматически)
```

---

## Часть 1 — Настройка на Windows (PyCharm)

### 1.1 Создание проекта в PyCharm

1. Открой **PyCharm → New Project**
2. Location: `C:\Users\ВашеИмя\lab1`
3. Interpreter: `New environment using Virtualenv` (Python 3.9+)
4. Нажми **Create**
5. Скопируй `server.py` и `client.py` в папку проекта

### 1.2 Запуск сервера на Windows

**Вариант A — через PyCharm:**
1. Правой кнопкой на `server.py` → **Run 'server'**

**Вариант B — через терминал (cmd или PowerShell):**
```cmd
cd C:\Users\ВашеИмя\lab1
python server.py
```

Сервер выведет:
```
[SERVER] Слушаю на 0.0.0.0:9090
```

### 1.3 Узнать IP адрес Windows машины

```cmd
ipconfig
```
Смотри строку **IPv4-адрес** нужного адаптера (например `192.168.1.10`).

---

## Часть 2 — Настройка на Fedora KDE Plasma

### 2.1 Установка Python (если нет)

```bash
sudo dnf install python3 python3-pip -y
```

### 2.2 Создание проекта

```bash
mkdir ~/lab1
cd ~/lab1
# скопируй сюда server.py и client.py
```

Чтобы скопировать с Windows через сеть (если в одной локальной сети):
```bash
scp user@192.168.1.10:C:/Users/ВашеИмя/lab1/*.py ~/lab1/
```
Или просто скопируй через USB/общую папку.

### 2.3 Запуск сервера на Fedora

```bash
cd ~/lab1
python3 server.py
```

### 2.4 Узнать IP адрес Fedora машины

```bash
ip addr show
# или
hostname -I
```

---

## Часть 3 — Тестирование в сети (2 машины)

### Схема стенда

```
[Windows — PyCharm]          [Fedora KDE]
   192.168.1.10                192.168.1.20
   
   Запускаем сервер      ИЛИ   Запускаем сервер
   на одной машине             на другой
   
   Клиент — на второй          Клиент — на первой
```

### 3.1 Запуск сервера (например, на Fedora)

```bash
# На Fedora:
cd ~/lab1
python3 server.py
# Вывод: [SERVER] Слушаю на 0.0.0.0:9090
```

### 3.2 Запуск клиента (на Windows)

```cmd
# На Windows:
cd C:\Users\ВашеИмя\lab1
python client.py 192.168.1.20 9090
```

Или с PyCharm: **Run → Edit Configurations → Parameters:** `192.168.1.20 9090`

### 3.3 Тестирование команд через клиент

```
> ECHO Привет мир
[SERVER] Привет мир

> TIME
[SERVER] 2024-11-01 14:32:05

> CLOSE
[SERVER] BYE
```

### 3.4 Тестирование через telnet (Windows)

Если telnet не включён: **Панель управления → Программы → Включить компоненты Windows → Telnet-клиент**

```cmd
telnet 192.168.1.20 9090
```
Затем вводишь команды вручную: `ECHO тест`, `TIME`, `CLOSE`

### 3.5 Тестирование через netcat (Fedora)

```bash
# Простой тест команд:
nc 192.168.1.10 9090

# Отправить команду и выйти:
echo -e "TIME\r\nCLOSE\r\n" | nc 192.168.1.10 9090
```

---

## Часть 4 — Тест передачи файлов

### 4.1 Создать тестовый файл

**На Windows:**
```cmd
fsutil file createnew test_file.bin 10485760
# создаёт файл 10 МБ
```

**На Fedora:**
```bash
dd if=/dev/urandom of=test_file.bin bs=1M count=10
# создаёт файл 10 МБ случайных данных
```

### 4.2 Загрузить файл на сервер (UPLOAD)

Положи `test_file.bin` рядом с `client.py`, затем:
```
> UPLOAD test_file.bin
[CLIENT] Докачка с байта 0 из 10485760
[CLIENT] Отправлено 10485760/10485760 байт
[CLIENT] OK размер=10485760 скорость=XXXX КБ/с
```

Файл появится в папке `server_files/` на сервере.

### 4.3 Скачать файл с сервера (DOWNLOAD)

```
> DOWNLOAD test_file.bin
[CLIENT] Скачиваю 'test_file.bin' (10485760 байт), оффсет 0
[CLIENT] Получено 10485760/10485760 байт
[CLIENT] Скачано в 'downloads/test_file.bin', скорость: XXXX КБ/с
```

### 4.4 Тест докачки (имитация обрыва)

1. Начни UPLOAD большого файла
2. Нажми Ctrl+C на клиенте в процессе передачи
3. Перезапусти клиент и снова введи `UPLOAD test_file.bin`
4. Клиент увидит `OFFSET XXXX` и продолжит с места обрыва ✓

---

## Часть 5 — Утилиты для демонстрации (обязательно на защите!)

### 5.1 nmap — сканирование портов сервера

**На Windows (скачать nmap.org) или Fedora:**
```bash
# Установка на Fedora:
sudo dnf install nmap -y

# Сканировать порты сервера:
nmap -sV 192.168.1.20
nmap -p 9090 192.168.1.20

# Подробнее (медленнее но точнее):
nmap -sV -p 1-10000 192.168.1.20
```

Пример вывода:
```
PORT     STATE SERVICE VERSION
9090/tcp open  zeus-admin?
```

### 5.2 netstat — список открытых сокетов

**Windows:**
```cmd
netstat -an | findstr 9090
netstat -b -n          # с именем процесса (требует прав администратора)
```

**Fedora:**
```bash
ss -tlnp | grep 9090
netstat -tlnp | grep 9090     # если установлен net-tools
sudo dnf install net-tools -y  # установить net-tools если нет
```

Пример вывода на сервере пока клиент подключён:
```
tcp  LISTEN  0  5  0.0.0.0:9090  0.0.0.0:*      server.py
tcp  ESTAB   0  0  192.168.1.20:9090  192.168.1.10:XXXXX
```

---

## Часть 6 — Возможные проблемы

### Порт занят
```bash
# Fedora: узнать кто занял порт
sudo lsof -i :9090
sudo kill -9 <PID>
```
```cmd
# Windows:
netstat -ano | findstr 9090
taskkill /PID <PID> /F
```

### Брандмауэр блокирует соединение

**Windows:**
```
Панель управления → Брандмауэр Windows Defender → 
Дополнительные параметры → Правила для входящих подключений → 
Создать правило → Порт → TCP → 9090 → Разрешить
```

**Fedora:**
```bash
sudo firewall-cmd --add-port=9090/tcp --permanent
sudo firewall-cmd --reload
# или временно отключить файрволл для теста:
sudo systemctl stop firewalld
```

### Проверка связи между машинами
```bash
ping 192.168.1.10    # пингуем Windows с Fedora
ping 192.168.1.20    # пингуем Fedora с Windows
```

---

## Быстрая шпаргалка команд

| Команда | Что делает |
|---------|-----------|
| `ECHO <текст>` | Сервер возвращает текст обратно |
| `TIME` | Сервер возвращает своё текущее время |
| `UPLOAD <имя файла>` | Клиент отправляет файл на сервер |
| `DOWNLOAD <имя файла>` | Клиент скачивает файл с сервера |
| `CLOSE` / `EXIT` / `QUIT` | Закрыть соединение |
