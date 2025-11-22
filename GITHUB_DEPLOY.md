# Публикация проекта на GitHub

## Шаг 1: Создайте репозиторий на GitHub

1. Перейдите на https://github.com/new
2. Заполните:
   - **Repository name**: `bigquery-metadata-manager` (или другое название)
   - **Description**: "Automated BigQuery table and column description generator using OpenAI"
   - **Visibility**: Public (для open source)
   - **НЕ** ставьте галочки на "Add a README file", "Add .gitignore", "Choose a license" (все это уже есть в проекте)
3. Нажмите "Create repository"

## Шаг 2: Добавьте GitHub remote

После создания репозитория GitHub покажет инструкции. Выполните одну из команд:

### Через SSH (рекомендуется):
```bash
git remote add github git@github.com:ВАШ_USERNAME/bigquery-metadata-manager.git
```

### Или через HTTPS:
```bash
git remote add github https://github.com/ВАШ_USERNAME/bigquery-metadata-manager.git
```

**Замените `ВАШ_USERNAME` и `bigquery-metadata-manager` на ваши значения!**

## Шаг 3: Запушьте код

```bash
# Запушить ветку open-source-prep на GitHub
git push github open-source-prep

# Создать main ветку на GitHub из open-source-prep
git push github open-source-prep:main

# Установить main как основную ветку (опционально)
git push github open-source-prep:main --set-upstream
```

## Шаг 4: Настройте репозиторий на GitHub

1. Перейдите в Settings → General → Default branch
2. Измените default branch на `main`
3. Перейдите в Settings → Pages (если нужен GitHub Pages)
4. Добавьте описание репозитория
5. Добавьте topics: `bigquery`, `openai`, `metadata`, `python`, `fastapi`

## Шаг 5: Создайте Release (опционально)

1. Перейдите в Releases → Create a new release
2. Tag version: `v1.0.0`
3. Release title: `v1.0.0 - Initial Release`
4. Описание можно взять из README.md
5. Publish release

## Проверка

После публикации ваш репозиторий будет доступен по адресу:
`https://github.com/ВАШ_USERNAME/bigquery-metadata-manager`

## Дополнительные настройки

### Добавить GitHub Actions для CI/CD (опционально)

Создайте файл `.github/workflows/ci.yml`:

```yaml
name: CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: python -m pytest  # если будут тесты
```

### Добавить badges в README

После публикации можно добавить badges в начало README.md:

```markdown
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
```

