// Toggle dataset section
function toggleDataset(dataset) {
    const content = document.getElementById(`content-${dataset}`);
    const icon = document.getElementById(`icon-${dataset}`);
    
    if (content.style.display === 'none') {
        content.style.display = 'block';
        icon.textContent = '▼';
    } else {
        content.style.display = 'none';
        icon.textContent = '▶';
    }
}

// Filter tables on index page
function filterTables() {
    const input = document.getElementById('searchInput');
    const filter = input.value.toLowerCase();
    
    // Get all dataset sections
    const datasetSections = document.querySelectorAll('.dataset-section');
    
    datasetSections.forEach(section => {
        const datasetName = section.getAttribute('data-dataset').toLowerCase();
        const tables = section.querySelectorAll('.tables-table tbody tr');
        let hasVisibleTables = false;
        
        // Check if dataset name matches
        const datasetMatches = datasetName.includes(filter);
        
        // Check tables
        tables.forEach(row => {
            const tableName = row.querySelector('.table-name')?.textContent.toLowerCase() || '';
            const description = row.querySelector('.description')?.textContent.toLowerCase() || '';
            
            if (datasetMatches || tableName.includes(filter) || description.includes(filter)) {
                row.style.display = '';
                hasVisibleTables = true;
            } else {
                row.style.display = 'none';
            }
        });
        
        // Auto-expand if matches filter
        if (hasVisibleTables || datasetMatches) {
            const dataset = section.getAttribute('data-dataset');
            const content = document.getElementById(`content-${dataset}`);
            const icon = document.getElementById(`icon-${dataset}`);
            if (content.style.display === 'none') {
                content.style.display = 'block';
                icon.textContent = '▼';
            }
        }
    });
}

// Filter columns on table detail page
function filterColumns() {
    const input = document.getElementById('columnSearch');
    if (!input) return;
    
    const filter = input.value.toLowerCase();
    const table = document.getElementById('columnsBody');
    if (!table) return;
    
    const rows = table.getElementsByTagName('tr');

    for (let i = 0; i < rows.length; i++) {
        const columnName = rows[i].querySelector('.column-name')?.textContent.toLowerCase() || '';
        
        if (columnName.includes(filter)) {
            rows[i].style.display = '';
        } else {
            rows[i].style.display = 'none';
        }
    }
}

// Save table description
async function saveTableDescription() {
    const description = document.getElementById('tableDescription').value;
    const statusEl = document.getElementById('tableSaveStatus');
    
    // Extract dataset and table_name from URL: /table/{dataset}/{table_name}
    const pathParts = window.location.pathname.split('/');
    const dataset = pathParts[2];
    const tableName = pathParts[3];
    const url = `/api/table/${dataset}/${tableName}/description`;
    
    statusEl.textContent = 'Сохранение...';
    statusEl.className = 'save-status';
    
    try {
        const formData = new FormData();
        formData.append('description', description);
        
        const response = await fetch(url, {
            method: 'POST',
            body: formData
        });
        
        if (response.ok) {
            statusEl.textContent = '✓ Сохранено';
            statusEl.className = 'save-status';
            setTimeout(() => {
                statusEl.textContent = '';
            }, 3000);
        } else {
            const error = await response.json();
            statusEl.textContent = '✗ Ошибка: ' + (error.detail || 'Неизвестная ошибка');
            statusEl.className = 'save-status error';
        }
    } catch (error) {
        statusEl.textContent = '✗ Ошибка соединения';
        statusEl.className = 'save-status error';
    }
}

// Save column description
async function saveColumnDescription(dataset, tableName, columnName) {
    const textarea = document.querySelector(
        `textarea[data-dataset="${dataset}"][data-table="${tableName}"][data-column="${columnName}"]`
    );
    
    if (!textarea) return;
    
    const description = textarea.value;
    const statusEl = document.getElementById(`status-${dataset}-${tableName}-${columnName}`);
    
    statusEl.textContent = 'Сохранение...';
    statusEl.className = 'save-status';
    
    try {
        const formData = new FormData();
        formData.append('description', description);
        
        const response = await fetch(
            `/api/table/${dataset}/${tableName}/column/${columnName}/description`,
            {
                method: 'POST',
                body: formData
            }
        );
        
        if (response.ok) {
            statusEl.textContent = '✓ Сохранено';
            statusEl.className = 'save-status';
            setTimeout(() => {
                statusEl.textContent = '';
            }, 3000);
        } else {
            const error = await response.json();
            statusEl.textContent = '✗ Ошибка: ' + (error.detail || 'Неизвестная ошибка');
            statusEl.className = 'save-status error';
        }
    } catch (error) {
        statusEl.textContent = '✗ Ошибка соединения';
        statusEl.className = 'save-status error';
    }
}

// Variables to store generation context
let pendingGenerateAction = null;

// Show info modal before generating table description
function showGenerateTableInfo() {
    const pathParts = window.location.pathname.split('/');
    const dataset = pathParts[2];
    const tableName = pathParts[3];
    
    const modal = document.getElementById('generateInfoModal');
    const content = document.getElementById('modalInfoContent');
    
    content.innerHTML = `
        <div class="info-section">
            <h3>📋 Что будет сделано:</h3>
            <ol style="line-height: 1.8;">
                <li><strong>Получение sample данных:</strong>
                    <ul style="margin-top: 5px; margin-left: 20px;">
                        <li>Запрос к BigQuery: <code>TABLESAMPLE SYSTEM (2 PERCENT)</code></li>
                        <li>Получение первых 5 строк данных из таблицы</li>
                        <li>Форматирование данных для включения в промпт</li>
                    </ul>
                </li>
                <li><strong>Формирование промпта для OpenAI:</strong>
                    <ul style="margin-top: 5px; margin-left: 20px;">
                        <li>Информация о таблице: <code>${dataset}.${tableName}</code></li>
                        <li>Список колонок с типами данных</li>
                        <li>Sample данные (первые 3 строки)</li>
                        <li>Инструкции для AI по генерации описания</li>
                    </ul>
                </li>
                <li><strong>Генерация через OpenAI:</strong>
                    <ul style="margin-top: 5px; margin-left: 20px;">
                        <li>Модель: GPT-4o-mini (или настроенная модель)</li>
                        <li>Температура: 0.6 (баланс между креативностью и точностью)</li>
                        <li>Максимум токенов: 300</li>
                    </ul>
                </li>
                <li><strong>Сохранение результата:</strong>
                    <ul style="margin-top: 5px; margin-left: 20px;">
                        <li>Автоматическое сохранение в таблицу метаданных</li>
                        <li>Обновление описания в BigQuery schema</li>
                        <li>Отображение стоимости запроса</li>
                    </ul>
                </li>
            </ol>
        </div>
        <div class="info-section" style="margin-top: 20px; padding: 15px; background: #f8f9fa; border-radius: 4px;">
            <p><strong>💰 Стоимость:</strong> Примерно $0.0001-0.0005 за запрос (зависит от размера таблицы)</p>
            <p><strong>⏱ Время:</strong> Обычно 2-5 секунд</p>
        </div>
    `;
    
    pendingGenerateAction = { type: 'table', dataset, tableName };
    modal.style.display = 'block';
}

// Show info modal before generating column description
function showGenerateColumnInfo(dataset, tableName, columnName) {
    const modal = document.getElementById('generateInfoModal');
    const content = document.getElementById('modalInfoContent');
    
    content.innerHTML = `
        <div class="info-section">
            <h3>📋 Что будет сделано:</h3>
            <ol style="line-height: 1.8;">
                <li><strong>Получение sample значений колонки:</strong>
                    <ul style="margin-top: 5px; margin-left: 20px;">
                        <li>Запрос к BigQuery: <code>SELECT DISTINCT \`${columnName}\` FROM ... TABLESAMPLE SYSTEM (2 PERCENT)</code></li>
                        <li>Получение уникальных значений (до 5 примеров)</li>
                        <li>Форматирование значений для промпта</li>
                    </ul>
                </li>
                <li><strong>Формирование промпта для OpenAI:</strong>
                    <ul style="margin-top: 5px; margin-left: 20px;">
                        <li>Информация о таблице: <code>${dataset}.${tableName}</code></li>
                        <li>Название колонки: <code>${columnName}</code></li>
                        <li>Тип данных колонки</li>
                        <li>Sample значения (если доступны)</li>
                        <li>Инструкции для AI по генерации описания колонки</li>
                    </ul>
                </li>
                <li><strong>Генерация через OpenAI:</strong>
                    <ul style="margin-top: 5px; margin-left: 20px;">
                        <li>Модель: GPT-4o-mini (или настроенная модель)</li>
                        <li>Температура: 0.5 (более точное описание)</li>
                        <li>Максимум токенов: 200</li>
                    </ul>
                </li>
                <li><strong>Сохранение результата:</strong>
                    <ul style="margin-top: 5px; margin-left: 20px;">
                        <li>Автоматическое сохранение в таблицу метаданных</li>
                        <li>Обновление описания колонки в BigQuery schema</li>
                        <li>Отображение стоимости запроса</li>
                    </ul>
                </li>
            </ol>
        </div>
        <div class="info-section" style="margin-top: 20px; padding: 15px; background: #f8f9fa; border-radius: 4px;">
            <p><strong>💰 Стоимость:</strong> Примерно $0.00005-0.0002 за запрос</p>
            <p><strong>⏱ Время:</strong> Обычно 1-3 секунды</p>
        </div>
    `;
    
    pendingGenerateAction = { type: 'column', dataset, tableName, columnName };
    modal.style.display = 'block';
}

// Close modal
function closeGenerateInfoModal() {
    document.getElementById('generateInfoModal').style.display = 'none';
    pendingGenerateAction = null;
}

// Confirm and proceed with generation
function confirmGenerate() {
    closeGenerateInfoModal();
    
    if (!pendingGenerateAction) return;
    
    if (pendingGenerateAction.type === 'table') {
        generateTableDescription();
    } else if (pendingGenerateAction.type === 'column') {
        generateColumnDescription(
            pendingGenerateAction.dataset,
            pendingGenerateAction.tableName,
            pendingGenerateAction.columnName
        );
    }
}

// Close modal when clicking outside
window.onclick = function(event) {
    const modal = document.getElementById('generateInfoModal');
    if (event.target === modal) {
        closeGenerateInfoModal();
    }
}

// Generate table description using AI
async function generateTableDescription() {
    const statusEl = document.getElementById('tableGenerateStatus');
    const descriptionEl = document.getElementById('tableDescription');
    
    const pathParts = window.location.pathname.split('/');
    const dataset = pathParts[2];
    const tableName = pathParts[3];
    const url = `/api/table/${dataset}/${tableName}/generate-description`;
    
    statusEl.textContent = 'Генерация...';
    statusEl.className = 'save-status';
    descriptionEl.disabled = true;
    
    try {
        const response = await fetch(url, {
            method: 'POST'
        });
        
        if (response.ok) {
            const data = await response.json();
            descriptionEl.value = data.description;
            statusEl.textContent = `✓ Сгенерировано ($${data.cost.toFixed(4)}, ${data.tokens.prompt + data.tokens.completion} токенов)`;
            statusEl.className = 'save-status';
            descriptionEl.disabled = false;
            
            setTimeout(() => {
                statusEl.textContent = '';
            }, 5000);
        } else {
            const error = await response.json();
            statusEl.textContent = '✗ Ошибка: ' + (error.detail || 'Неизвестная ошибка');
            statusEl.className = 'save-status error';
            descriptionEl.disabled = false;
        }
    } catch (error) {
        statusEl.textContent = '✗ Ошибка соединения';
        statusEl.className = 'save-status error';
        descriptionEl.disabled = false;
    }
}

// Generate column description using AI
async function generateColumnDescription(dataset, tableName, columnName) {
    const statusEl = document.getElementById(`status-${dataset}-${tableName}-${columnName}`);
    const textarea = document.querySelector(
        `textarea[data-dataset="${dataset}"][data-table="${tableName}"][data-column="${columnName}"]`
    );
    
    if (!textarea) return;
    
    statusEl.textContent = 'Генерация...';
    statusEl.className = 'save-status';
    textarea.disabled = true;
    
    try {
        const response = await fetch(
            `/api/table/${dataset}/${tableName}/column/${columnName}/generate-description`,
            {
                method: 'POST'
            }
        );
        
        if (response.ok) {
            const data = await response.json();
            textarea.value = data.description;
            statusEl.textContent = `✓ Сгенерировано ($${data.cost.toFixed(4)})`;
            statusEl.className = 'save-status';
            textarea.disabled = false;
            
            setTimeout(() => {
                statusEl.textContent = '';
            }, 5000);
        } else {
            const error = await response.json();
            statusEl.textContent = '✗ Ошибка: ' + (error.detail || 'Неизвестная ошибка');
            statusEl.className = 'save-status error';
            textarea.disabled = false;
        }
    } catch (error) {
        statusEl.textContent = '✗ Ошибка соединения';
        statusEl.className = 'save-status error';
        textarea.disabled = false;
    }
}
