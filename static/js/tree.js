$(document).ready(function() {
    loadInitialTree();

    let snDataTable = null;

    // --- 1. 核心：加载 DataTables ---
    window.loadMonthLogs = function(pn, month) {
        // 获取当前年份（通常从树结构或全局变量获取，这里默认为今年，或者你可以根据业务需求调整）
        const year = new Date().getFullYear();
        $('#tableTitle').html(`<i class="bi bi-calendar-check me-2"></i> ${pn} [${year}-${month}]`);

        if ($.fn.DataTable.isDataTable('#snTable')) {
            $('#snTable').DataTable().destroy();
        }

        snDataTable = $('#snTable').DataTable({
            // 切换到新的合并接口
            ajax: {
                url: `/api/get_month_logs`,
                data: { pn: pn, month: month, year: year },
                dataSrc: 'data'
            },
            columns: [
                {
                    data: null,
                    orderable: false,
                    render: function(data, type, row) {
                        return `<input type="checkbox" class="row-check" data-srv="${row.server}" data-path="${row.path}">`;
                    }
                },
                { data: 'sn' },
                {
                    data: 'status',
                    render: function(data) {
                        const cls = data === 'PASS' ? 'text-success' : 'text-danger';
                        return `<span class="fw-bold ${cls}">${data}</span>`;
                    }
                },
                { data: 'stage' },
                { data: 'last_time' }, // 这里的列名已在后端接口对齐
                {
                    data: null,
                    orderable: false,
                    render: function(data, type, row) {
                        return `
                            <div class="btn-group">
                                <button class="btn btn-xs btn-outline-primary py-0" onclick="openPreview('${row.server}', '${row.path}')">View</button>
                                <a href="${row.download_url}" class="btn btn-xs btn-outline-success py-0">Down</a>
                            </div>`;
                    }
                }
            ],
            pageLength: 15,
            order: [[4, 'desc']], // 按 Time (last_time) 列倒序排列，最新的在上面
            drawCallback: function() {
                updateBatchUI();
            }
        });

        $('#snTable tbody').off('change', '.row-check').on('change', '.row-check', updateBatchUI);
    };

    // --- 2. 批量操作逻辑 (保持不变) ---
    $('#selectAll').on('change', function() {
        $('.row-check').prop('checked', this.checked);
        updateBatchUI();
    });

    function updateBatchUI() {
        let selected = $('.row-check:checked');
        $('#selCount').text(selected.length);
        if (selected.length > 0) { $('#btnBatchDown').fadeIn(); }
        else { $('#btnBatchDown').fadeOut(); }
    }

    window.handleBatchDownload = function() {
        let selectedFiles = [];
        $('.row-check:checked').each(function() {
            selectedFiles.push({
                server: $(this).data('srv'),
                path: $(this).data('path')
            });
        });
        if (selectedFiles.length === 0) return;

        const btn = $('#btnBatchDown');
        const originalHtml = btn.html();
        btn.prop('disabled', true).html('<span class="spinner-border spinner-border-sm"></span> 打包中...');

        fetch('/api/batch_download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ files: selectedFiles })
        })
        .then(response => {
            if (!response.ok) throw new Error('打包失败');
            return response.blob();
        })
        .then(blob => {
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `logs_batch_${new Date().getTime()}.zip`;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
        })
        .catch(error => alert(error.message))
        .finally(() => {
            btn.prop('disabled', false).html(originalHtml);
        });
    };

    // --- 3. 预览逻辑 (保持不变) ---
    window.openPreview = function(server, path) {
        $('#previewContent').text('Loading from Memory...');
        $('#previewModal').modal('show');
        $.get('/api/preview_log', {server: server, path: path}, function(data) {
            if (data.content) {
                $('#previewTitle').text("Preview: " + data.filename);
                $('#previewContent').text(data.content);
            } else {
                $('#previewContent').text("Error: " + data.error);
            }
        });
    };

    window.saveLogFromMemory = function() {
        // 1. 解决警告：移除当前焦点
        if (document.activeElement) {
            document.activeElement.blur();
        }

        const text = $('#previewContent').text();
        if (!text || text === 'Loading...') return;

        const title = $('#previewTitle').text().replace("Preview: ", "").trim();
        const filename = title || 'log_export.log';

        const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
        const url = window.URL.createObjectURL(blob);

        const a = document.createElement('a');
        a.href = url;
        a.download = filename.endsWith('.log') ? filename : filename + '.log';

        // 2. 解决不安全链接警告：确保 DOM 周期完整并延时释放
        document.body.appendChild(a);
        a.click();

        setTimeout(() => {
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url); // 释放内存
        }, 200);
    };

    // --- 4. 树形菜单逻辑 (已删除 loadDays) ---
    function toggleBsCollapse(id) {
        let el = document.getElementById(id);
        if (el) bootstrap.Collapse.getOrCreateInstance(el).toggle();
    }

    function loadInitialTree() {
        $.get('/api/get_tree_base', function(data) {
            let html = '';
            for (let server in data) {
                html += `<div class="tree-node border-bottom py-1">
                    <div class="node-content fw-bold" onclick="toggleNode('${server}')">
                        <i class="bi bi-caret-right-fill collapse-icon me-1" id="icon_${server}"></i>
                        <i class="bi bi-hdd-network text-primary me-2"></i> ${server}
                    </div>
                    <div class="collapse ms-3" id="child_${server}">
                        ${data[server].map(share => `
                            <div class="tree-node">
                                <div class="node-content text-secondary" onclick="loadPNs('${server}', '${share}')">
                                    <i class="bi bi-caret-right-fill collapse-icon me-1" id="icon_${server}_${share}"></i>
                                    <i class="bi bi-folder-fill text-warning me-2"></i> ${share}
                                </div>
                                <div class="collapse ms-3" id="child_${server}_${share}"></div>
                            </div>
                        `).join('')}
                    </div>
                </div>`;
            }
            $('#treeRoot').html(html);
        });
    }

    window.toggleNode = function(id) {
        $(`#icon_${id}`).toggleClass('rotate-90');
        toggleBsCollapse(`child_${id}`);
    };

    window.loadPNs = function(srv, shr) {
        let id = `child_${srv}_${shr}`;
        $(`#icon_${srv}_${shr}`).toggleClass('rotate-90');
        toggleBsCollapse(id);
        if ($(`#${id}`).html() === "") {
            $.get('/api/get_pns', {server: srv, share: shr}, function(pns) {
                $(`#${id}`).html(pns.map(pn => `
                    <div class="tree-node">
                        <div class="node-content text-info" onclick="loadMonths('${srv}', '${shr}', '${pn}')">
                            <i class="bi bi-caret-right-fill collapse-icon me-1" id="icon_${srv}_${shr}_${pn}"></i>
                            <i class="bi bi-box-seam me-2"></i> ${pn}
                        </div>
                        <div class="collapse ms-3 border-start ps-2" id="child_${srv}_${shr}_${pn}"></div>
                    </div>`).join(''));
            });
        }
    };

    // 核心变动：点击月份不再展开日期，而是直接刷新表格
    window.loadMonths = function(srv, shr, pn) {
        let id = `child_${srv}_${shr}_${pn}`;
        $(`#icon_${srv}_${shr}_${pn}`).toggleClass('rotate-90');
        toggleBsCollapse(id);
        if ($(`#${id}`).html() === "") {
            $.get('/api/get_months', {pn: pn}, function(mons) {
                $(`#${id}`).html(mons.map(mon => `
                    <div class="tree-node">
                        <div class="node-content text-muted small" onclick="loadMonthLogs('${pn}', '${mon}')">
                            <i class="bi bi-calendar-month me-2"></i> ${mon} Month
                        </div>
                    </div>`).join(''));
            });
        }
    };
});