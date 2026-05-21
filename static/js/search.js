$(document).ready(function() {
    // 1. 获取 URL 中的参数 (例如 ?s_sn=123&s_pn=ABC)
    const urlParams = new URLSearchParams(window.location.search);
    const sn = urlParams.get('s_sn'); // 可能为空（PN 搜索时）
    const pn = urlParams.get('s_pn'); // 始终有值

    if (sn || pn) {
        if (sn) $('#s_sn').val(sn);
        if (pn) $('#s_pn').val(pn);
        // 自动执行搜索
        setTimeout(() => { $('#btn_search').click(); }, 500);
    }

    // 初始化年份下拉框：带上多租户钥匙
    $.get('/api/get_years', { project_key: CURRENT_PROJECT_KEY }, function(data) {
        if (data.status === 'success') {
            let options = data.years.map(y => `<option value="${y}">${y} Year</option>`).join('');
            $('#s_year').append(options);
        }
    });

    // 初始化 Server 下拉框：带上多租户钥匙，防止拉错 BFT/ICT 资产
    $.get('/api/get_servers', { project_key: CURRENT_PROJECT_KEY }, function(data) {
        if (data.status === 'success') {
            let options = data.servers.map(s => `<option value="${s}">${s}</option>`).join('');
            $('#s_machine').append(options);
        } else {
            console.error("Failed to load servers:", data.message);
        }
    });

    // 初始化 DataTable
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
                // 💡 4. DataTable 服务端异步翻页/搜索时，自动注入隔离令牌
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
                    // row.server 和 row.path 是后端返回的原始字段
                    //下载链接 `href` 这里，必须使用 `?project_key=` 将钥匙传给后端下载路由
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

    // 搜索和重置按钮逻辑
    $('#btn_search').on('click', () => table.draw());
    $('#btn_reset').on('click', () => {
        $('#searchForm')[0].reset();
        table.draw();
    });

    // --- 预览功能函数 ---
    window.openPreview = function(server, path) {
        const contentArea = $('#previewContent');
        contentArea.text('Fetching from server RAM...');
        $('#previewModal').modal('show');

        //预览日志接口：同样追加多租户钥匙，保障物理防越权
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