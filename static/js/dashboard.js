$(document).ready(function() {
    // 异步从后台抓取服务器监控状态
    $.ajax({
        url: '/api/server_stats',
        type: 'GET',
        dataType: 'json',
        success: function(data) {
            $('#table-loader').addClass('d-none');
            if(!data || data.length === 0) {
                $('#stats-table-body').html('<tr><td colspan="6" class="text-center text-muted py-3">No ingestion data found.</td></tr>');
                $('#stats-table').removeClass('d-none');
                return;
            }

            var htmlStr = '';
            data.forEach(function(s) {
                var stageHtml = '';
                if(s.stage) {
                    var stageList = s.stage.split('|');
                    stageList.forEach(function(st) {
                        st = st.trim();
                        var badgeClass = 'bg-secondary-subtle text-secondary';
                        if(st === 'BFT') badgeClass = 'bg-info-subtle text-info';
                        else if(st === 'FQA') badgeClass = 'bg-warning-subtle text-warning';

                        stageHtml += '<span class="badge border me-1 ' + badgeClass + '" style="font-size: 0.7rem;">' + st + '</span>';
                    });
                }

                // 2. 昨日详情解析
                var yDetails = '';
                if(s.details && s.details.yesterday) {
                    var keys = Object.keys(s.details.yesterday);
                    keys.forEach(function(stg, idx) {
                        yDetails += stg + ':' + s.details.yesterday[stg];
                        if(idx < keys.length - 1) yDetails += ' | ';
                    });
                }

                // 3. 今日详情解析
                var tDetails = '';
                if(s.details && s.details.today) {
                    var keys = Object.keys(s.details.today);
                    keys.forEach(function(stg, idx) {
                        tDetails += stg + ':' + s.details.today[stg];
                        if(idx < keys.length - 1) tDetails += ' | ';
                    });
                }

                var formattedYesterday = Number(s.yesterday_count || 0).toLocaleString();
                var formattedToday = Number(s.today_count || 0).toLocaleString();

                htmlStr += '<tr>' +
                    '<td class="ps-4"><div class="fw-bold text-dark">' + s.server + '</div></td>' +
                    '<td class="text-center">' + stageHtml + '</td>' +
                    '<td class="text-center text-muted"><div>' + formattedYesterday + '</div><div style="font-size: 0.65rem; opacity: 0.8;">' + yDetails + '</div></td>' +
                    '<td class="text-center text-primary fw-bold"><div>' + formattedToday + '</div><div class="text-muted fw-normal" style="font-size: 0.65rem; margin-top: -2px;">' + tDetails + '</div></td>' +
                    '<td class="text-center"><span class="text-success fw-bold" style="font-size: 0.7rem;"><span class="spinner-grow spinner-grow-sm me-1" style="width: 6px; height: 6px;"></span>LIVE</span></td>' +
                    '<td class="text-end pe-4 text-muted font-monospace">' + (s.last_time || '-') + '</td>' +
                '</tr>';
            });

            $('#stats-table-body').html(htmlStr);
            $('#stats-table').removeClass('d-none');
        },
        error: function() {
            $('#table-loader').addClass('d-none');
            $('#stats-table-body').html('<tr><td colspan="6" class="text-center text-danger py-3">Failed to load monitoring data.</td></tr>');
            $('#stats-table').removeClass('d-none');
        }
    });
});