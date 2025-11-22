# Локальный запуск приложения

## Быстрый старт

### 1. Установите зависимости

```bash
pip install -r requirements.txt
```

### 2. Настройте переменные окружения

Создайте файл `.env` на основе `.env.example`:

```bash
cp .env.example .env
```

Заполните необходимые переменные в `.env`:

```bash
# Минимальные настройки для локального запуска
PROJECT_ID=your-gcp-project-id
METADATA_DATASET_ID=metadata
OPENAI_API_KEY=your-openai-api-key-here

# OAuth настройки (для веб-интерфейса)
GOOGLE_CLIENT_ID=your-google-oauth-client-id
GOOGLE_CLIENT_SECRET=your-google-oauth-client-secret
SECRET_KEY=generate-a-random-secret-key-here
REDIRECT_URI=http://localhost:8081/auth/callback
```

**Важно:** Для локального запуска `REDIRECT_URI` должен быть `http://localhost:8081/auth/callback`

### 3. Настройте Google OAuth

1. Перейдите в [Google Cloud Console](https://console.cloud.google.com/)
2. Выберите ваш проект
3. Перейдите в **APIs & Services** → **Credentials**
4. Создайте **OAuth 2.0 Client ID** (если еще не создан)
5. Добавьте в **Authorized redirect URIs**: `http://localhost:8081/auth/callback`
6. Скопируйте **Client ID** и **Client Secret** в `.env`

### 4. Настройте Application Default Credentials для BigQuery

```bash
gcloud auth application-default login
```

Это позволит приложению использовать ваши учетные данные для доступа к BigQuery.

### 5. Запустите приложение

```bash
python web_app.py
```

Или с указанием порта:

```bash
PORT=8081 python web_app.py
```

### 6. Откройте в браузере

Веб-интерфейс будет доступен по адресу: **http://localhost:8081**

## Устранение проблем

### Ошибка "Not authenticated"

- Убедитесь, что `GOOGLE_CLIENT_ID` и `GOOGLE_CLIENT_SECRET` правильно настроены
- Проверьте, что `REDIRECT_URI` в `.env` совпадает с URI в Google Cloud Console
- Убедитесь, что вы авторизованы через Google OAuth

### Ошибка доступа к BigQuery

- Проверьте, что выполнили `gcloud auth application-default login`
- Убедитесь, что у вашего аккаунта есть права на BigQuery в проекте
- Проверьте, что `PROJECT_ID` правильный

### Ошибка "OPENAI_API_KEY not found"

- Убедитесь, что `OPENAI_API_KEY` установлен в `.env`
- Проверьте, что файл `.env` находится в корне проекта

## Альтернативный запуск без OAuth (только для тестирования)

Если вы хотите протестировать приложение без настройки OAuth, можно временно отключить проверку аутентификации в коде, но это **не рекомендуется для production**.

