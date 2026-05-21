# app/utils/metrics.py
from datetime import datetime, timedelta
from sqlalchemy import text
from app import db

def get_server_stats():
    target_year = datetime.now().year
    table_name = f"log_index_{target_year}"

    now = datetime.now()
    today_date = now.date()
    yesterday_date = today_date - timedelta(days=1)

    start_time = yesterday_date.strftime('%Y-%m-%d 00:00:00')
    today_start = today_date.strftime('%Y-%m-%d 00:00:00')

    sql = text(f"""
        SELECT
            server_name,
            stage,
            SUM(CASE WHEN log_time >= :today_start THEN 1 ELSE 0 END) as t_count,
            SUM(CASE WHEN log_time < :today_start THEN 1 ELSE 0 END) as y_count,
            MAX(log_time) as last_up
        FROM `{table_name}`
        WHERE log_time >= :start
        GROUP BY server_name, stage
        ORDER BY last_up DESC
    """)

    try:
        result_proxy = db.session.execute(sql, {
            "today_start": today_start,
            "start": start_time
        })

        server_map = {}
        for row in result_proxy.mappings():
            sn = str(row['server_name']).upper()
            stg = row['stage'] or 'UNKNOWN'

            if sn not in server_map:
                server_map[sn] = {
                    'server': sn,
                    'stages': [],
                    'today_count': 0,
                    'yesterday_count': 0,
                    'last_dt': None,
                    'details': {'today': {}, 'yesterday': {}}
                }

            server_map[sn]['today_count'] += int(row['t_count'] or 0)
            server_map[sn]['yesterday_count'] += int(row['y_count'] or 0)

            if row['t_count'] > 0:
                server_map[sn]['details']['today'][stg] = int(row['t_count'])
            if row['y_count'] > 0:
                server_map[sn]['details']['yesterday'][stg] = int(row['y_count'])

            if stg not in server_map[sn]['stages']:
                server_map[sn]['stages'].append(stg)

            curr_last = row['last_up']
            if curr_last and (not server_map[sn]['last_dt'] or curr_last > server_map[sn]['last_dt']):
                server_map[sn]['last_dt'] = curr_last

        stats = []
        for sn in sorted(server_map.keys()):
            item = server_map[sn]
            ldt = item['last_dt']
            display_time = ldt.strftime('%H:%M:%S') if ldt and ldt.date() == today_date else (ldt.strftime('%m-%d %H:%M') if ldt else "N/A")

            stats.append({
                'server': sn,
                'stage': "|".join(item['stages']),
                'last_time': display_time,
                'today_count': item['today_count'],
                'yesterday_count': item['yesterday_count'],
                'details': item['details'],
                'status': 'Active'
            })
        return stats

    except Exception as e:
        print(f"Error in get_server_stats: {e}")
        return []