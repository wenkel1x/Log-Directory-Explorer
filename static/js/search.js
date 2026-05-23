$(document).ready(function() {
    // 1. 获取 URL 中的参数
    const urlParams = new URLSearchParams(window.location.search);
    const sn = urlParams.get('s_sn');
    const pn = urlParams.get('s_pn');

    if (sn) $('#s_sn').val(sn);
    if (pn) $('#s_pn').val(pn);

    // 2. 先请求年份和服务器数据，全部完成后再初始化 DataTable
    // 使用 Promise.all 保证所有下拉框都渲染完毕
    const loadYears = $.get('/api/get_years', { project_key: CURRENT_PROJECT_KEY });
    const loadServers = $.get('/api/get_servers', { project_key: CURRENT_PROJECT_KEY });

    Promise.all([loadYears, loadServers]).then(([yearsRes, serversRes]) => {
        // 处理年份下拉框
        if (yearsRes.status === 'success') {
            // 当不指定年份去搜 SN 时，后端就会自动遍历所有年份表
            let options = '<option value="">All Years</option>';
            options += yearsRes.years.map(y => `<option value="${y}">${y} Year</option>`).join('');
            $('#s_year').html(options);
        }

        // 处理 Server 下拉框
        if (serversRes.status === 'success') {
            let options = '<option value="">All Servers</option>';
            options += serversRes.servers.map(s => `<option value="${s}">${s}</option>`).join('');
            $('#s_machine').html(options);
        } else {
            console.error("Failed to load servers:", serversRes.message);
        }

        // 3. 下拉框就绪后，再初始化 DataTable
        initDataTable();

    }).catch(err => {
        console.error("Initialization failed:", err);
        // 如果接口挂了，也保底初始化一下，防止页面卡死
        initDataTable();
    });

    // 将 DataTable 封装为独立函数
    function initDataTable() {
        const table = $('#logTable').DataTable({
            processing: true,
            serverSide: true,
            ajax: {
                url: '/api/logs_server_side',
                type: 'POST',
                data: function(d) {
                    d.s_year = $('#s_year').val();
                    d.s_machine = $.trim($('#s_machine').val());
                    d.s_sn = $.trim($('#s_sn').val());
                    d.s_pn = $.trim($('#s_pn').val());
                    d.s_status = $('#s_status').val();
                    d.s_stage = $('#s_stage').val();
                    d.project_key = CURRENT_PROJECT_KEY;
                }
            },
            columns: [
                { data: 'log_time' },
                { data: 'server' },
                { data: 'pn' },
                { data: 'sn' },
                { data: 'stage' },
                {
                    data: 'status',
                    render: function(data) {
                        let cls = data === 'PASS' ? 'success' : 'danger';
                        return `<span class="badge bg-${cls}">${data}</span>`;
                    }
                },
                {
                    data: null,
                    orderable: false,
                    render: function(data, type, row) {
                        return `
                            <div class="btn-group">
                                <button class="btn btn-sm btn-outline-primary" onclick="openPreview('${row.server}', '${row.path}')">
                                    <i class="bi bi-eye"></i> View
                                </button>
                                <a href="/download/${row.server}/${row.path}?project_key=${CURRENT_PROJECT_KEY}" class="btn btn-sm btn-outline-success">
                                    <i class="bi bi-download"></i>
                                </a>
                            </div>
                        `;
                    }
                }
            ],
            pageLength: 25,
            order: [[0, 'desc']]
        });

        // 绑定搜索和重置按钮（因为 table 变量在作用域内，可以直接用）
        $('#btn_search').off('click').on('click', () => table.draw());
        $('#btn_reset').off('click').on('click', () => {
            $('#searchForm')[0].reset();
            table.draw();
        });
        $('#searchForm').find('input, select').on('keydown', function(e) {
            if (e.which === 13) {
                e.preventDefault(); // 阻止表单默认的提交刷新行为
                table.draw();       // 触发 DataTable 重新加载
            }
        });
        if (sn || pn) {
            table.draw();
        }
    }

    // --- 预览功能函数 ---
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

    window.saveLogFromMemory = function() {
        const text = $('#previewContent').text();
        const filename = $('#previewTitle').text().replace("File: ", "");
        const blob = new Blob([text], { type: 'text/plain' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = filename || 'log.txt'; a.click();
        window.URL.revokeObjectURL(url);
    };
});