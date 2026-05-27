// ==========================================
//  预览功能函数
// ==========================================
window.openPreview = function(server, path) {
    const contentArea = $('#previewContent');
    contentArea.text('Fetching from server RAM...');
    $('#previewModal').modal('show');

    $.get('/api/preview_log', {
        server: server,
        path: path,
        project_key: CURRENT_PROJECT_KEY
    }, function(data) {
        if (data.content) {
            $('#previewTitle').text("File: " + data.filename);
            contentArea.text(data.content);
        } else {
            contentArea.html(`<div class="p-4 text-danger">Error: ${data.error}</div>`);
        }
    }).fail(function() {
        contentArea.html('<div class="p-4 text-danger">Fetch failed.</div>');
    });
};

// ==========================================
// 2. 页面载入就绪逻辑
// ==========================================
$(document).ready(function() {
    // 1. 获取 URL 中的参数
    const urlParams = new URLSearchParams(window.location.search);
    const sn = urlParams.get('s_sn');
    const pn = urlParams.get('s_pn');

    if (sn) $('#s_sn').val(sn);
    if (pn) $('#s_pn').val(pn);

    // 2. 异步加载筛选菜单数据
    const loadYears = $.get('/api/get_years', { project_key: CURRENT_PROJECT_KEY });
    const loadServers = $.get('/api/get_servers', { project_key: CURRENT_PROJECT_KEY });

    Promise.all([loadYears, loadServers]).then(([yearsRes, serversRes]) => {
        if (yearsRes.status === 'success' && yearsRes.years) {
            let options = '<option value="">All Years</option>';
            options += yearsRes.years.map(y => `<option value="${y}">${y} Year</option>`).join('');
            $('#s_year').html(options);
        }
        if (serversRes.status === 'success' && serversRes.servers) {
            let options = '<option value="">All Servers</option>';
            options += serversRes.servers.map(s => `<option value="${s}">${s}</option>`).join('');
            $('#s_machine').html(options);
        }
        initDataTable();
    }).catch(err => {
        console.error("Init items failed, fallback to table layout:", err);
        initDataTable();
    });

    // ==========================================
    // 3. DataTable 初始化核心体
    // ==========================================
    function initDataTable() {
        const table = $('#logTable').DataTable({
            processing: true,
            serverSide: true,
            searching: false,
            ajax: {
                url: '/api/logs_server_side',
                type: 'POST',
                data: function(d) {
                    d.s_year = $('#s_year').val();
                    d.s_machine = $.trim($('#s_machine').val());
                    d.s_pn = $.trim($('#s_pn').val());
                    d.s_status = $('#s_status').val();
                    d.s_stage = $.trim($('#s_stage').val());
                    d.project_key = CURRENT_PROJECT_KEY;

                    // 清洗SN
                    let rawSn = $('#s_sn').val();
                    if (rawSn) {
                        d.s_sn = rawSn
                            .replace(/[\s,，;；\n\r\t]+/g, ',')
                            .replace(/^,|,$/g, '');
                    } else {
                        d.s_sn = '';
                    }
                },
                // 前端批量未匹配 SN 通知提示
                dataSrc: function(json) {
                    if (json.missing_sns && json.missing_sns.length > 0) {
                        const errMsg = `⚠️ ${json.missing_sns.length} SN(s) not found in the current year's database. Please verify:\n\n` + json.missing_sns.join('\n');
                        alert(errMsg);
                    }
                    return json.data;
                }
            },
            columns: [
                {
                    data: null,
                    orderable: false,
                    className: 'text-center',
                    render: function(data, type, row) {
                        return `<input type="checkbox" class="form-check-input row-checkbox" data-server="${row.server}" data-path="${row.path}">`;
                    }
                },
                { data: 'log_time' },
                { data: 'server' },
                { data: 'pn' },
                { data: 'sn' },
                { data: 'stage' },
                {
                    data: 'status',
                    render: function(data) {
                        let cls = data === 'PASS' ? 'success' : 'danger';
                        return `<span class="badge bg-${cls} small">${data}</span>`;
                    }
                },
                {
                    data: null,
                    orderable: false,
                    render: function(data, type, row) {
                        return `
                            <div class="btn-group">
                                <button type="button" class="btn btn-sm btn-outline-primary py-0" onclick="openPreview('${row.server}', '${row.path}')"><i class="bi bi-eye"></i> View</button>
                                <a href="/api/download/${row.server}/${row.path}?project_key=${CURRENT_PROJECT_KEY}" class="btn btn-sm btn-outline-success py-0"><i class="bi bi-download"></i></a>
                            </div>
                        `;
                    }
                }
            ],
            pageLength: 25,
            lengthMenu: [10, 25, 50, 100],
            order: [[1, 'desc']],
            drawCallback: function() {
                $('#check_all').prop('checked', false);
                toggleBatchButton();
                const btn = $('#btn_search');
                btn.prop('disabled', false).text('Apply');
                $('#logTable').css('opacity', '1');
            }
        });

        setTimeout(() => {
            $(".dataTables_length select").addClass("form-select form-select-sm d-inline-block w-auto ms-1 me-1");
        }, 50);

        $('body').off('change', '#check_all').on('change', '#check_all', function() {
            $('.row-checkbox').prop('checked', this.checked);
            toggleBatchButton();
        });

        $('#logTable tbody').off('change', '.row-checkbox').on('change', '.row-checkbox', function() {
            const allChecked = $('.row-checkbox').length === $('.row-checkbox:checked').length;
            $('#check_all').prop('checked', allChecked);
            toggleBatchButton();
        });

        function toggleBatchButton() {
            const checkedCount = $('.row-checkbox:checked').length;
            if (checkedCount > 0) {
                $('#selected_count').text(checkedCount);
                $('#btn_batch_download').show();
            } else {
                $('#btn_batch_download').hide();
            }
        }

        // ==========================================
        // 搜索过滤控制逻辑
        // ==========================================
        function triggerSearch() {
            const btn = $('#btn_search');
            // 如果已经在搜索中，防止重复点击
            if (btn.prop('disabled')) return;
            // 1. 把按钮变成 Loading 状态
            btn.prop('disabled', true)
               .html(`<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Searching...`);
            // 2. 让表格本身变半透明，暗示数据正在刷新
            $('#logTable').css('opacity', '0.5');
            // 3. 让 DataTable 重新抓取数据
            table.draw();
        }
        $('#btn_search').off('click').on('click', triggerSearch);
        $('#btn_reset').off('click').on('click', () => {
            $('#searchForm')[0].reset();
            table.draw();
        });

        $('#searchForm').find('input, textarea').off('keydown').on('keydown', function(e) {
            if (e.which === 13 && !e.shiftKey) {
                e.preventDefault();
                triggerSearch();
            }
        });
        $('#searchForm').find('select').off('change').on('change', triggerSearch);

        // ==========================================
        // 批量打包下载
        // ==========================================
        $('#btn_batch_download').off('click').on('click', function() {
            const btn = $(this);
            let files = [];
            $('.row-checkbox:checked').each(function() {
                files.push({
                    server: $(this).data('server'),
                    path: $(this).data('path')
                });
            });

            if(files.length === 0) return;
            btn.prop('disabled', true).html(`<span class="spinner-border spinner-border-sm"></span> Zipping...`);

            fetch('/api/batch_download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    project_key: CURRENT_PROJECT_KEY,
                    files: files
                })
            })
            .then(response => {
                if (!response.ok) throw new Error('Network file pack response failed');
                return response.blob();
            })
            .then(blob => {
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                const timeStr = new Date().toISOString().slice(0,16).replace(/[-T:]/g,"");
                a.href = url;
                a.download = `batch_logs_${timeStr}.zip`;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);
            })
            .catch(err => {
                alert("Batch download failed: " + err.message);
            })
            .finally(() => {
                btn.prop('disabled', false).html(`<i class="bi bi-file-earmark-zip"></i> Batch Download (<span id="selected_count">${$('.row-checkbox:checked').length}</span>)`);
            });
        });
    }
});