// Filter tables on index page
function filterTables() {
    const input = document.getElementById('searchInput');
    const filter = input.value.toLowerCase();
    const table = document.getElementById('tablesBody');
    const rows = table.getElementsByTagName('tr');

    for (let i = 0; i < rows.length; i++) {
        const dataset = rows[i].querySelector('.dataset')?.textContent.toLowerCase() || '';
        const tableName = rows[i].querySelector('.table-name')?.textContent.toLowerCase() || '';
        
        if (dataset.includes(filter) || tableName.includes(filter)) {
            rows[i].style.display = '';
        } else {
            rows[i].style.display = 'none';
        }
    }
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
    const url = window.location.pathname + '/description';
    
    statusEl.textContent = 'Сохранение...';
    statusEl.className = 'save-status';
    
    try {
        const formData = new FormData();
        formData.append('description', description);
        
        const response = await fetch('/api' + url, {
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

