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
    
    statusEl.textContent = 'Saving...';
    statusEl.className = 'save-status';
    
    try {
        const formData = new FormData();
        formData.append('description', description);
        
        const response = await fetch(url, {
            method: 'POST',
            body: formData
        });
        
        if (response.ok) {
            statusEl.textContent = '✓ Saved';
            statusEl.className = 'save-status';
            setTimeout(() => {
                statusEl.textContent = '';
            }, 3000);
        } else {
            const error = await response.json();
            statusEl.textContent = '✗ Error: ' + (error.detail || 'Unknown error');
            statusEl.className = 'save-status error';
        }
    } catch (error) {
        statusEl.textContent = '✗ Connection error';
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
    
    statusEl.textContent = 'Saving...';
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
            statusEl.textContent = '✓ Saved';
            statusEl.className = 'save-status';
            setTimeout(() => {
                statusEl.textContent = '';
            }, 3000);
        } else {
            const error = await response.json();
            statusEl.textContent = '✗ Error: ' + (error.detail || 'Unknown error');
            statusEl.className = 'save-status error';
        }
    } catch (error) {
        statusEl.textContent = '✗ Connection error';
        statusEl.className = 'save-status error';
    }
}

// Save all column descriptions
async function saveAllColumnDescriptions() {
    const pathParts = window.location.pathname.split('/');
    const dataset = pathParts[2];
    const tableName = pathParts[3];
    
    // Get all textareas with column descriptions
    const textareas = document.querySelectorAll(
        `textarea[data-dataset="${dataset}"][data-table="${tableName}"][data-column]`
    );
    
    if (textareas.length === 0) {
        alert('No columns to save');
        return;
    }
    
    const statusEl = document.getElementById('saveAllStatus');
    const saveButton = document.querySelector('button[onclick="saveAllColumnDescriptions()"]');
    
    if (!saveButton) {
        console.error('Save button not found');
        return;
    }
    
    const originalButtonText = saveButton.textContent;
    
    // Disable button and show progress
    saveButton.disabled = true;
    saveButton.textContent = '💾 Saving...';
    
    let savedCount = 0;
    let errorCount = 0;
    const errors = [];
    
    statusEl.innerHTML = `<div style="padding: 10px; background: #f0f0f0; border-radius: 4px; font-size: 12px;">
        <strong>🔄 Saving column descriptions...</strong><br>
        <div id="saveProgress" style="margin-top: 5px;">Processed: 0 / ${textareas.length}</div>
    </div>`;
    statusEl.className = 'save-status';
    
    // Save each column description sequentially
    for (let i = 0; i < textareas.length; i++) {
        const textarea = textareas[i];
        const columnName = textarea.getAttribute('data-column');
        const description = textarea.value.trim();
        
        // Skip empty descriptions
        if (!description) {
            savedCount++;
            updateProgress(savedCount, textareas.length, errorCount);
            continue;
        }
        
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
                savedCount++;
                // Update individual status
                const individualStatus = document.getElementById(`status-${dataset}-${tableName}-${columnName}`);
                if (individualStatus) {
                    individualStatus.textContent = '✓ Saved';
                    individualStatus.className = 'save-status';
                }
            } else {
                errorCount++;
                const error = await response.json();
                errors.push(`${columnName}: ${error.detail || 'Unknown error'}`);
                
                // Update individual status
                const individualStatus = document.getElementById(`status-${dataset}-${tableName}-${columnName}`);
                if (individualStatus) {
                    individualStatus.textContent = '✗ Error';
                    individualStatus.className = 'save-status error';
                }
            }
        } catch (error) {
            errorCount++;
            errors.push(`${columnName}: Connection error`);
            
            // Update individual status
            const individualStatus = document.getElementById(`status-${dataset}-${tableName}-${columnName}`);
            if (individualStatus) {
                individualStatus.textContent = '✗ Error';
                individualStatus.className = 'save-status error';
            }
        }
        
        // Update progress
        updateProgress(savedCount, textareas.length, errorCount);
        
        // Small delay to avoid overwhelming the server
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    
    // Show final result
    saveButton.disabled = false;
    saveButton.textContent = originalButtonText;
    
    if (errorCount === 0) {
        statusEl.innerHTML = `<div style="padding: 10px; background: #e8f5e9; border-radius: 4px; font-size: 12px;">
            <strong>✅ Successfully saved!</strong><br>
            Saved descriptions: ${savedCount} of ${textareas.length}
        </div>`;
        statusEl.className = 'save-status';
        
        setTimeout(() => {
            statusEl.innerHTML = '';
        }, 5000);
    } else {
        statusEl.innerHTML = `<div style="padding: 10px; background: #ffebee; border-radius: 4px; font-size: 12px; color: #c62828;">
            <strong>⚠️ Save completed with errors</strong><br>
            Success: ${savedCount} of ${textareas.length}<br>
            Errors: ${errorCount}<br>
            ${errors.length > 0 ? '<div style="margin-top: 5px; font-size: 11px;">' + errors.slice(0, 5).join('<br>') + (errors.length > 5 ? '<br>... and ' + (errors.length - 5) + ' more errors' : '') + '</div>' : ''}
        </div>`;
        statusEl.className = 'save-status error';
    }
}

// Helper function to update progress
function updateProgress(saved, total, errors) {
    const progressEl = document.getElementById('saveProgress');
    if (progressEl) {
        progressEl.textContent = `Processed: ${saved} / ${total}${errors > 0 ? ` (errors: ${errors})` : ''}`;
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
            <h3>📋 What will be done:</h3>
            <ol style="line-height: 1.8;">
                <li><strong>Get sample data:</strong>
                    <ul style="margin-top: 5px; margin-left: 20px;">
                        <li>Query BigQuery: <code>TABLESAMPLE SYSTEM (2 PERCENT)</code></li>
                        <li>Get first 5 rows of data from the table</li>
                        <li>Format data for inclusion in the prompt</li>
                    </ul>
                </li>
                <li><strong>Form OpenAI prompt:</strong>
                    <ul style="margin-top: 5px; margin-left: 20px;">
                        <li>Table information: <code>${dataset}.${tableName}</code></li>
                        <li>List of columns with data types</li>
                        <li>Sample data (first 3 rows)</li>
                        <li>Instructions for AI to generate description</li>
                    </ul>
                </li>
                <li><strong>Generate via OpenAI:</strong>
                    <ul style="margin-top: 5px; margin-left: 20px;">
                        <li>Model: GPT-4o-mini (or configured model)</li>
                        <li>Temperature: 0.6 (balance between creativity and accuracy)</li>
                        <li>Max tokens: 300</li>
                    </ul>
                </li>
                <li><strong>Save result:</strong>
                    <ul style="margin-top: 5px; margin-left: 20px;">
                        <li>Automatically save to metadata table</li>
                        <li>Update description in BigQuery schema</li>
                        <li>Display request cost</li>
                    </ul>
                </li>
            </ol>
        </div>
        <div class="info-section" style="margin-top: 20px; padding: 15px; background: #f8f9fa; border-radius: 4px;">
            <p><strong>💰 Cost:</strong> Approximately $0.0001-0.0005 per request (depends on table size)</p>
            <p><strong>⏱ Time:</strong> Usually 2-5 seconds</p>
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
            <h3>📋 What will be done:</h3>
            <ol style="line-height: 1.8;">
                <li><strong>Get sample column values:</strong>
                    <ul style="margin-top: 5px; margin-left: 20px;">
                        <li>Query BigQuery: <code>SELECT DISTINCT \`${columnName}\` FROM ... TABLESAMPLE SYSTEM (2 PERCENT)</code></li>
                        <li>Get unique values (up to 5 examples)</li>
                        <li>Format values for the prompt</li>
                    </ul>
                </li>
                <li><strong>Form OpenAI prompt:</strong>
                    <ul style="margin-top: 5px; margin-left: 20px;">
                        <li>Table information: <code>${dataset}.${tableName}</code></li>
                        <li>Column name: <code>${columnName}</code></li>
                        <li>Column data type</li>
                        <li>Sample values (if available)</li>
                        <li>Instructions for AI to generate column description</li>
                    </ul>
                </li>
                <li><strong>Generate via OpenAI:</strong>
                    <ul style="margin-top: 5px; margin-left: 20px;">
                        <li>Model: GPT-4o-mini (or configured model)</li>
                        <li>Temperature: 0.5 (more accurate description)</li>
                        <li>Max tokens: 200</li>
                    </ul>
                </li>
                <li><strong>Save result:</strong>
                    <ul style="margin-top: 5px; margin-left: 20px;">
                        <li>Automatically save to metadata table</li>
                        <li>Update column description in BigQuery schema</li>
                        <li>Display request cost</li>
                    </ul>
                </li>
            </ol>
        </div>
        <div class="info-section" style="margin-top: 20px; padding: 15px; background: #f8f9fa; border-radius: 4px;">
            <p><strong>💰 Cost:</strong> Approximately $0.00005-0.0002 per request</p>
            <p><strong>⏱ Time:</strong> Usually 1-3 seconds</p>
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
    // Save data before closing modal
    const action = pendingGenerateAction;
    
    if (!action) {
        console.error('No pending generate action');
        return;
    }
    
    // Close modal
    closeGenerateInfoModal();
    
    // Execute generation
    if (action.type === 'table') {
        generateTableDescription();
    } else if (action.type === 'column') {
        generateColumnDescription(
            action.dataset,
            action.tableName,
            action.columnName
        );
    } else {
        console.error('Unknown action type:', action.type);
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
    
    // Show detailed progress
    let progressHtml = '<div style="margin-top: 10px; padding: 10px; background: #f0f0f0; border-radius: 4px; font-size: 12px;">';
    progressHtml += '<strong>🔄 Generation process:</strong><ul style="margin: 5px 0; padding-left: 20px;">';
    progressHtml += '<li>Connecting to BigQuery...</li>';
    progressHtml += '<li>Getting table information...</li>';
    progressHtml += '</ul></div>';
    
    statusEl.innerHTML = progressHtml;
    statusEl.className = 'save-status';
    descriptionEl.disabled = true;
    
    try {
        const response = await fetch(url, {
            method: 'POST'
        });
        
        if (response.ok) {
            const data = await response.json();
            descriptionEl.value = data.description;
            
            // Show detailed success message with stats
            let successHtml = '<div style="margin-top: 10px; padding: 10px; background: #e8f5e9; border-radius: 4px; font-size: 12px;">';
            successHtml += '<strong>✅ Successfully generated!</strong><br>';
            
            if (data.steps) {
                successHtml += '<div style="margin-top: 8px;"><strong>Execution steps:</strong><ul style="margin: 5px 0; padding-left: 20px;">';
                data.steps.forEach(step => {
                    successHtml += `<li>${step}</li>`;
                });
                successHtml += '</ul></div>';
            }
            
            if (data.stats) {
                successHtml += '<div style="margin-top: 8px;"><strong>📊 Statistics:</strong><ul style="margin: 5px 0; padding-left: 20px;">';
                successHtml += `<li>Total columns: ${data.stats.total_columns}</li>`;
                if (data.stats.sensitive_columns > 0) {
                    successHtml += `<li>Sensitive columns: ${data.stats.sensitive_columns}</li>`;
                }
                if (data.stats.sample_rows > 0) {
                    successHtml += `<li>Sample rows: ${data.stats.sample_rows}</li>`;
                    successHtml += `<li>Sample columns: ${data.stats.sample_columns}</li>`;
                }
                successHtml += `<li>Model: ${data.stats.model}</li>`;
                successHtml += `<li>Prompt length: ${data.stats.prompt_length} characters</li>`;
                successHtml += '</ul></div>';
            }
            
            successHtml += `<div style="margin-top: 8px;"><strong>💰 Cost:</strong> $${data.cost.toFixed(4)}`;
            successHtml += ` (${data.tokens.prompt} prompt + ${data.tokens.completion} completion = ${data.tokens.prompt + data.tokens.completion} tokens)</div>`;
            successHtml += '</div>';
            
            statusEl.innerHTML = successHtml;
            statusEl.className = 'save-status';
            descriptionEl.disabled = false;
            
            setTimeout(() => {
                statusEl.innerHTML = '';
            }, 15000); // Show for 15 seconds
        } else {
            const error = await response.json();
            statusEl.innerHTML = `<div style="margin-top: 10px; padding: 10px; background: #ffebee; border-radius: 4px; color: #c62828;"><strong>✗ Error:</strong> ${error.detail || 'Unknown error'}</div>`;
            statusEl.className = 'save-status error';
            descriptionEl.disabled = false;
        }
    } catch (error) {
        statusEl.innerHTML = `<div style="margin-top: 10px; padding: 10px; background: #ffebee; border-radius: 4px; color: #c62828;"><strong>✗ Connection error:</strong> ${error.message}</div>`;
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
    
    // Show progress
    statusEl.innerHTML = '<div style="font-size: 11px; color: #666;">🔄 Generating...</div>';
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
            
            // Show detailed success message
            let successHtml = '<div style="font-size: 11px; padding: 5px; background: #e8f5e9; border-radius: 3px; margin-top: 5px;">';
            successHtml += '<strong>✅ Generated!</strong><br>';
            
            if (data.steps) {
                successHtml += '<div style="margin-top: 5px; font-size: 10px;">';
                data.steps.forEach((step, idx) => {
                    if (idx < 3) { // Show first 3 steps
                        successHtml += `${step}<br>`;
                    }
                });
                if (data.steps.length > 3) {
                    successHtml += `... and ${data.steps.length - 3} more steps<br>`;
                }
                successHtml += '</div>';
            }
            
            if (data.stats) {
                successHtml += `<div style="margin-top: 5px; font-size: 10px;">`;
                successHtml += `Type: ${data.stats.data_type}`;
                if (data.stats.sample_values_count > 0) {
                    successHtml += ` | Sample: ${data.stats.sample_values_count}`;
                }
                successHtml += ` | Model: ${data.stats.model}`;
                successHtml += '</div>';
            }
            
            successHtml += `<div style="margin-top: 5px; font-size: 10px; font-weight: bold;">💰 $${data.cost.toFixed(4)} (${data.tokens.prompt + data.tokens.completion} tokens)</div>`;
            successHtml += '</div>';
            
            statusEl.innerHTML = successHtml;
            statusEl.className = 'save-status';
            textarea.disabled = false;
            
            setTimeout(() => {
                statusEl.innerHTML = '';
            }, 12000); // Show for 12 seconds
        } else {
            const error = await response.json();
            statusEl.innerHTML = `<div style="font-size: 11px; padding: 5px; background: #ffebee; border-radius: 3px; color: #c62828;"><strong>✗ Error:</strong> ${error.detail || 'Unknown error'}</div>`;
            statusEl.className = 'save-status error';
            textarea.disabled = false;
        }
    } catch (error) {
        statusEl.innerHTML = `<div style="font-size: 11px; padding: 5px; background: #ffebee; border-radius: 3px; color: #c62828;"><strong>✗ Connection error:</strong> ${error.message}</div>`;
        statusEl.className = 'save-status error';
        textarea.disabled = false;
    }
}
