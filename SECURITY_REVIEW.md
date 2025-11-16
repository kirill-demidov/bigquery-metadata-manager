# Анализ безопасности: Отправка данных в OpenAI

## 🔴 КРИТИЧЕСКИЕ ПРОБЛЕМЫ

### 1. ⚠️ Sample данные отправляются БЕЗ маскирования значений

**Проблема:** При генерации описаний таблиц и колонок в OpenAI отправляются **реальные значения данных** из таблиц без маскирования.

**Затронутые функции:**
- `get_table_sample_data()` - получает реальные данные через `SELECT *`
- `format_sample_data_for_prompt()` - форматирует данные для промпта
- `generate_table_description()` - отправляет sample данные в OpenAI
- `generate_column_description()` - отправляет sample значения колонок в OpenAI

**Пример утечки:**
```python
# Текущий код отправляет:
Example 1: user_id=12345, email=john.doe@example.com, phone=+1234567890, credit_card=****1234
```

**Риск:** **КРИТИЧЕСКИЙ** - утечка PII данных в сторонний сервис (OpenAI)

**Что отправляется:**
- Email адреса
- Телефонные номера
- ID пользователей
- Любые другие данные из таблиц
- До 3 строк данных × до 5 колонок = до 15 значений на запрос

---

### 2. ⚠️ Нет фильтрации чувствительных колонок из sample данных

**Проблема:** Даже если колонка называется `user_email` или содержит PII данные, она все равно включается в sample данные.

**Текущая защита:**
- ✅ Маскируются только **имена** колонок (если содержат паттерны типа `pii`, `email`)
- ❌ **Значения** колонок отправляются как есть

**Пример:**
```python
# Колонка называется "user_id" (не содержит паттерн "pii")
# Но содержит реальные ID пользователей: [12345, 67890, 11111]
# Эти значения отправляются в OpenAI БЕЗ маскирования
```

---

### 3. ⚠️ Нет проверки типов данных для маскирования

**Проблема:** Не проверяется тип данных колонки перед отправкой. Колонки типа STRING могут содержать:
- Email адреса
- Телефонные номера
- Кредитные карты
- Токены
- Пароли (хэши)

**Риск:** Высокий - утечка структурированных PII данных

---

### 4. ⚠️ Ограничение только по длине строки (100 символов)

**Текущая защита:**
```python
elif isinstance(value, str) and len(value) > 100:
    row_dict[col] = value[:100] + "..."
```

**Проблема:**
- Email адрес (30 символов) → отправляется полностью
- Телефон (15 символов) → отправляется полностью
- ID (10 символов) → отправляется полностью
- Числовые значения → отправляются полностью

---

## 📊 Анализ текущего кода

### Функция `get_table_sample_data()`

```python
def get_table_sample_data(bq_client, dataset_id, table_name, sample_percent=2, max_rows=5):
    # ❌ SELECT * - получает ВСЕ колонки без фильтрации
    query = f"""
    SELECT *
    FROM `{PROJECT_ID}.{dataset_id}.{table_name}`
    TABLESAMPLE SYSTEM ({sample_percent} PERCENT)
    LIMIT {max_rows}
    """
    
    # ❌ Только обрезка длинных строк, но значения отправляются как есть
    if isinstance(value, str) and len(value) > 100:
        row_dict[col] = value[:100] + "..."
    else:
        row_dict[col] = value  # ⚠️ Реальное значение отправляется
```

**Что отправляется в OpenAI:**
- Все колонки таблицы
- До 5 строк данных
- Реальные значения (только обрезка >100 символов)

---

### Функция `format_sample_data_for_prompt()`

```python
def format_sample_data_for_prompt(sample_data, max_examples=3):
    # ❌ Форматирует все значения без маскирования
    row_str = ", ".join([f"{k}={v}" for k, v in row.items() if v is not None])
    # Пример: "user_id=12345, email=john@example.com, phone=+1234567890"
```

**Риск:** Все значения попадают в промпт OpenAI

---

### Функция генерации описания колонки

```python
# ❌ Получает DISTINCT значения колонки
query = f"""
SELECT DISTINCT `{column_name}`
FROM `{PROJECT_ID}.{dataset_id}.{table_name}`
TABLESAMPLE SYSTEM (2 PERCENT)
WHERE `{column_name}` IS NOT NULL
LIMIT 5
"""

# ❌ Отправляет реальные значения
values = [str(v)[:50] for v in df_values[column_name].head(5).tolist() if v is not None]
sample_values_text = f"\nSample values: {', '.join(values)}"
```

**Риск:** Уникальные значения колонки (email, phone, ID) отправляются в OpenAI

---

## 🛡️ Рекомендации по исправлению

### 1. Маскирование значений в sample данных

```python
def mask_sensitive_value(value, column_name):
    """Маскирование чувствительных значений"""
    if value is None:
        return None
    
    value_str = str(value)
    
    # Проверка паттернов в значениях
    if re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', value_str):
        # Email - маскировать
        return "***@***.***"
    
    if re.match(r'^\+?[1-9]\d{1,14}$', value_str) or re.match(r'^\d{10,}$', value_str):
        # Телефон - маскировать
        return "***-***-****"
    
    if re.match(r'^\d{13,19}$', value_str):
        # Кредитная карта - маскировать
        return "****-****-****-****"
    
    # Проверка по имени колонки
    if is_sensitive_name(column_name):
        # Маскировать все значения чувствительных колонок
        if isinstance(value, (int, float)):
            return "***"
        elif isinstance(value, str):
            return "***" if len(value_str) < 20 else value_str[:3] + "***"
    
    return value
```

### 2. Исключение чувствительных колонок из sample данных

```python
def get_table_sample_data(bq_client, dataset_id, table_name, sample_percent=2, max_rows=5):
    # Получить список колонок
    table_ref = bq_client.get_table(f"{PROJECT_ID}.{dataset_id}.{table_name}")
    
    # Исключить чувствительные колонки
    safe_columns = [
        field.name for field in table_ref.schema 
        if not is_sensitive_name(field.name)
    ]
    
    if not safe_columns:
        return None  # Все колонки чувствительные - не отправлять данные
    
    # SELECT только безопасных колонок
    columns_str = ", ".join([f"`{col}`" for col in safe_columns])
    query = f"""
    SELECT {columns_str}
    FROM `{PROJECT_ID}.{dataset_id}.{table_name}`
    TABLESAMPLE SYSTEM ({sample_percent} PERCENT)
    LIMIT {max_rows}
    """
```

### 3. Маскирование значений перед форматированием

```python
def format_sample_data_for_prompt(sample_data, max_examples=3, column_names=None):
    examples = sample_data[:max_examples]
    formatted = []
    for i, row in enumerate(examples, 1):
        row_items = []
        for k, v in row.items():
            if v is not None:
                # Маскировать значение перед отправкой
                masked_value = mask_sensitive_value(v, k)
                row_items.append(f"{k}={masked_value}")
        row_str = ", ".join(row_items)
        formatted.append(f"Example {i}: {row_str}")
    return "\n".join(formatted)
```

### 4. Расширение паттернов чувствительных данных

```python
SENSITIVE_COLUMN_PATTERNS = [
    r'.*pii.*', r'.*personal.*', r'.*ssn.*', r'.*password.*',
    r'.*credit.*card.*', r'.*payment.*', r'.*secret.*', r'.*token.*',
    r'.*auth.*', r'.*credential.*',
    r'.*email.*', r'.*phone.*', r'.*mobile.*', r'.*tel.*',
    r'.*user.*id.*', r'.*customer.*id.*', r'.*account.*id.*',
    r'.*address.*', r'.*zip.*', r'.*postal.*',
    r'.*ip.*address.*', r'.*mac.*address.*'
]

SENSITIVE_VALUE_PATTERNS = {
    'email': r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$',
    'phone': r'^\+?[1-9]\d{1,14}$|^\d{10,}$',
    'credit_card': r'^\d{13,19}$',
    'ssn': r'^\d{3}-\d{2}-\d{4}$',
    'ip_address': r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$'
}
```

---

## 📋 План исправлений

### Приоритет 1 (КРИТИЧНО) - Немедленно

1. ✅ **Исключить чувствительные колонки из sample данных**
   - Не отправлять колонки с паттернами `email`, `phone`, `pii`, `user_id` и т.д.
   - Если все колонки чувствительные - не отправлять sample данные вообще

2. ✅ **Маскировать значения перед отправкой**
   - Email → `***@***.***`
   - Телефон → `***-***-****`
   - ID → `***`
   - Длинные строки → первые 3 символа + `***`

3. ✅ **Расширить список чувствительных паттернов**
   - Добавить `email`, `phone`, `user_id`, `customer_id`, `account_id`

### Приоритет 2 (Высокий) - В ближайшее время

4. ✅ **Проверка типов данных**
   - Если колонка типа STRING и содержит email-подобные значения → маскировать
   - Если колонка типа INTEGER и содержит ID → маскировать

5. ✅ **Логирование попыток отправки чувствительных данных**
   - Логировать, когда колонки исключаются
   - Логировать, когда значения маскируются

### Приоритет 3 (Средний) - Улучшения

6. ✅ **Настройка через конфигурацию**
   - Whitelist колонок, которые можно отправлять
   - Blacklist колонок, которые нельзя отправлять
   - Настройка уровня маскирования

---

## 🔍 Примеры утечек данных

### Пример 1: Таблица пользователей

**Таблица:** `users`
**Колонки:** `user_id`, `email`, `phone`, `name`, `created_at`

**Что отправляется сейчас:**
```
Example 1: user_id=12345, email=john.doe@example.com, phone=+1234567890, name=John Doe, created_at=2024-01-01
Example 2: user_id=67890, email=jane.smith@example.com, phone=+0987654321, name=Jane Smith, created_at=2024-01-02
```

**Риск:** Утечка email адресов и телефонных номеров в OpenAI

---

### Пример 2: Таблица платежей

**Таблица:** `payments`
**Колонки:** `payment_id`, `user_id`, `amount`, `credit_card_last4`, `status`

**Что отправляется сейчас:**
```
Example 1: payment_id=1001, user_id=12345, amount=99.99, credit_card_last4=1234, status=completed
```

**Риск:** Утечка информации о платежах и связь user_id с payment_id

---

### Пример 3: Таблица сессий

**Таблица:** `user_sessions`
**Колонки:** `session_id`, `user_id`, `ip_address`, `user_agent`, `created_at`

**Что отправляется сейчас:**
```
Example 1: session_id=abc123, user_id=12345, ip_address=192.168.1.1, user_agent=Mozilla/5.0..., created_at=2024-01-01
```

**Риск:** Утечка IP адресов и session ID

---

## ✅ Что уже реализовано (хорошо)

1. ✅ Маскирование имен таблиц и колонок
2. ✅ Параметризованные SQL запросы
3. ✅ Валидация имен таблиц и колонок
4. ✅ Фильтрация по whitelist/blacklist датасетов
5. ✅ OpenAI API ключ в Secret Manager

---

## 🚨 Критичность исправлений

**Текущий риск:** **КРИТИЧЕСКИЙ**

- Реальные данные из таблиц отправляются в OpenAI
- Нет маскирования значений
- Нет исключения чувствительных колонок
- Может нарушать GDPR/CCPA требования

**Рекомендация:** Немедленно исправить перед использованием в production с реальными данными.

