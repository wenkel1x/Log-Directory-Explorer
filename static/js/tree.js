$(document).ready(function() {
    let snDataTable = null;

    // --- 树形菜单初始化 ---
    window.loadInitialTree = function() {
        // 加载基础树：带上多租户钥匙，让后端只返回当前业务线的服务器资产
        $.get('/api/get_tree_base', { project_key: CURRENT_PROJECT_KEY }, function(data) {
            let html = '';
            for (let server in data) {
                html += `
                <div class="tree-node border-bottom py-1">
                    <div class="node-content fw-bold" onclick="toggleNode('${server}')" style="cursor:pointer">
                        <i class="bi bi-caret-right-fill collapse-icon me-1" id="icon_${server}"></i>
                        <i class="bi bi-hdd-network text-primary me-2"></i> ${server}
                    </div>
                    <div class="collapse ms-3" id="child_${server}">
                        ${data[server].map(share => `
                            <div class="tree-node">
                                <div class="node-content text-secondary" onclick="loadPNs('${server}', '${share}')" style="cursor:pointer">
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
    };

    loadInitialTree(); // 执行初始化

    // --- 树形逐级加载逻辑 ---
    window.toggleNode = function(id) {
        $(`#icon_${id}`).toggleClass('rotate-90');
        toggleBsCollapse(`child_${id}`);
    };

    window.loadPNs = function(srv, shr) {
        let id = `child_${srv}_${shr}`;
        $(`#icon_${srv}_${shr}`).toggleClass('rotate-90');
        toggleBsCollapse(id);
        if ($(`#${id}`).html().trim() === "") {
            // 加载 PN 列表：带上多租户钥匙
            $.get('/api/get_pns', { server: srv, share: shr, project_key: CURRENT_PROJECT_KEY }, function(pns) {
                $(`#${id}`).html(pns.map(pnObj => {
                    const pnName = pnObj.name; 
                    const hasData = pnObj.has_data;
                    const textColor = hasData ? 'text-info' : 'text-muted';
                    const clickAction = hasData ? `onclick="loadMonths('${srv}', '${shr}', '${pnName}')"` : '';
                    const iconClass = hasData ? '' : 'd-none'; 
                    const statusIcon = hasData ? '' : '<i class="bi bi-hand-index-thumb text-muted opacity-50" style="font-size: 1rem; transform: rotate(90deg); display: inline-block; vertical-align: middle; margin-left: 8px;" title="No data"></i>';
                    // 跳转高级搜索：把 `/bft/search` 统一改为 `/search`，并带上当前 project_key 令牌
                    const tracePnUrl = `/search?s_pn=${encodeURIComponent(pnName)}&project_key=${CURRENT_PROJECT_KEY}`;

                    return `
                        <div class="tree-node">
                            <div class="d-flex justify-content-between align-items-center pe-2">
                                <div class="node-content ${textColor}" ${clickAction} style="cursor:${hasData ? 'pointer' : 'default'}">
                                    <i class="bi bi-caret-right-fill collapse-icon me-1 ${iconClass}" id="icon_${srv}_${shr}_${pnName}"></i>
                                    <i class="bi bi-box-seam me-2"></i> 
                                    ${pnName} ${statusIcon}
                                </div>
                                <a href="${tracePnUrl}" class="text-info" title="Trace this PN">
                                    <i class="bi bi-search" style="font-size: 0.8rem;"></i>
                                </a>
                            </div>
                            <div class="collapse ms-3 border-start ps-2" id="child_${srv}_${shr}_${pnName}"></div>
                        </div>`;
                }).join(''));
            });
        }
    };

    window.loadMonths = function(srv, shr, pn) {
        let id = `child_${srv}_${shr}_${pn}`;
        $(`#icon_${srv}_${shr}_${pn}`).toggleClass('rotate-90');
        toggleBsCollapse(id);
        if ($(`#${id}`).html().trim() === "") {
            //加载月份：带上多租户钥匙，保障表映射安全
            $.get('/api/get_months', { pn: pn, project_key: CURRENT_PROJECT_KEY }, function(mons) {
                $(`#${id}`).html(mons.map(mon => `
                    <div class="tree-node py-1">
                        <div class="node-content text-muted small" onclick="loadMonthLogs('${pn}', '${mon.num}')" style="cursor:pointer">
                            <i class="bi bi-calendar-month me-2"></i> ${mon.name}
                        </div>
                    </div>`).join(''));
            });
        }
    };

    function toggleBsCollapse(id) {
        let el = document.getElementById(id);
        if (el) bootstrap.Collapse.getOrCreateInstance(el).toggle();
    }

    // ---  DataTables 加载日志列表 (核心预览触发) ---
    window.loadMonthLogs = function(pn, month) {
        const year = new Date().getFullYear();
        $('#tableTitle').html(`<i class="bi bi-calendar-check me-2"></i> ${pn} [${year}-${month}]`);

        if ($.fn.DataTable.isDataTable('#snTable')) {
            $('#snTable').DataTable().destroy();
        }

        snDataTable = $('#snTable').DataTable({
            ajax: {
                url: `/api/get_month_logs`,
                // DataTable 获取日志明细：必须将 project_key 传给后端
                data: { pn: pn, month: month, year: year, project_key: CURRENT_PROJECT_KEY },
                dataSrc: 'data'
            },
            columns: [
                {
                    // 1. 复选框列
                    data: null,
                    orderable: false,
                    render: function(data, type, row) {
                        return `<input type="checkbox" class="row-check" data-srv="${row.server}" data-path="${row.path}">`;
                    }
                },
                {
                    // 2. SN 列
                    data: 'sn'
                },
                {
                    // 3. Status 列
                    data: 'status',
                    render: function(data) {
                        const cls = data === 'PASS' ? 'text-success' : 'text-danger';
                        return `<span class="fw-bold ${cls}">${data}</span>`;
                    }
                },
                {
                    // 4. Stage 列
                    data: 'stage'
                },
                {
                    // 5. Time 列
                    data: 'last_time'
                },
                {
                    // 6. 操作列
                    data: null,
                    orderable: false,
                    render: function(data, type, row) {
                        //跳转高级搜索：将 `/bft/search` 规范为 `/search`，且追加隔离钥匙
                        const searchUrl = `/search?s_sn=${encodeURIComponent(row.sn)}&s_pn=${encodeURIComponent(pn)}&project_key=${CURRENT_PROJECT_KEY}`;

                        // 下载链接：也需要动态挂载 `?project_key=`
                        const downloadUrl = `${row.download_url}?project_key=${CURRENT_PROJECT_KEY}`;

                        return `
                        <div class="btn-group">
                            <button class="btn btn-xs btn-outline-primary py-0" onclick="openPreview('${row.server}', '${row.path}')">View</button>
                            <a href="${downloadUrl}" class="btn btn-xs btn-outline-success py-0">Down</a>
                            <a href="${searchUrl}" class="btn btn-xs btn-outline-info py-0" title="Trace this SN in Global Search">
                                <i class="bi bi-search"></i>
                            </a>
                        </div>`;
                    }
                }
            ],
            pageLength: 15,
            order: [[4, 'desc']],
            drawCallback: function() { updateBatchUI(); }
        });

        $('#snTable tbody').off('change', '.row-check').on('change', '.row-check', updateBatchUI);
    };

    // --- 预览逻辑 (Modal) ---
    window.openPreview = function(server, path) {
        $('#previewContent').text('Loading from Memory...');
        $('#previewModal').modal('show');

        //预览日志接口：追加隔离令牌
        $.get('/api/preview_log', { server: server, path: path, project_key: CURRENT_PROJECT_KEY }, function(data) {
            if (data.content) {
                $('#previewTitle').text("Preview: " + data.filename);
                $('#previewContent').text(data.content);
            } else {
                $('#previewContent').text("Error: " + (data.error || "Failed to load log content."));
            }
        });
    };

    window.saveLogFromMemory = function() {
        if (document.activeElement) document.activeElement.blur();
        const text = $('#previewContent').text();
        if (!text || text.startsWith('Loading')) return;

        const title = $('#previewTitle').text().replace("Preview: ", "").trim();
        const filename = title || 'log_export.log';
        const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        setTimeout(() => {
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
        }, 200);
    };

    // --- 批量下载逻辑 ---
    function updateBatchUI() {
        let selected = $('.row-check:checked');
        $('#selCount').text(selected.length);
        if (selected.length > 0) $('#btnBatchDown').fadeIn();
        else $('#btnBatchDown').fadeOut();
    }

    $('#selectAll').on('change', function() {
        $('.row-check').prop('checked', this.checked);
        updateBatchUI();
    });

    window.handleBatchDownload = function() {
        let selectedFiles = [];
        $('.row-check:checked').each(function() {
            selectedFiles.push({ server: $(this).data('srv'), path: $(this).data('path') });
        });
        if (selectedFiles.length === 0) return;

        const btn = $('#btnBatchDown');
        const originalHtml = btn.html();
        btn.prop('disabled', true).html('<span class="spinner-border spinner-border-sm"></span> 打包中...');

        //批量下载接口：在 POST 的 JSON Payload 中注入 project_key 令牌
        fetch('/api/batch_download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                files: selectedFiles,
                project_key: CURRENT_PROJECT_KEY
            })
        })
        .then(res => res.blob())
        .then(blob => {
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `logs_batch_${Date.now()}.zip`;
            a.click();
        })
        .finally(() => { btn.prop('disabled', false).html(originalHtml); });
    };
});