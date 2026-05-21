from flask import Blueprint, render_template, jsonify
main_bp = Blueprint('main_bp', __name__)
@main_bp.route('/')
def index():
    projects = [
        {
            'name': 'SMT BFT Log System',
            'desc': 'BFT Station log center, supporting tree view and advanced search.',
            'status': 'Active',
            'tree_url': '/explorer?project_key=log_system',
            'search_url': '/search?project_key=log_system',
            'icon': 'bi-cpu-fill',
            'color': 'primary'
        },
        {
            'name': 'SMT ICT Log System',
            'desc': 'ICT Station log center, supporting tree view and advanced search.',
            'status': 'Active',
            'tree_url': '/explorer?project_key=ict_log_System',
            'search_url': '/search?project_key=ict_log_System',
            'icon': 'bi-motherboard',
            'color': 'success'
        },
        {
            'name': 'BFT Log Analysis',
            'desc': 'BFT test log analysis module is currently in the environment debugging phase.',
            'status': 'Pending',
            'tree_url': None,
            'search_url': None,
            'icon': 'bi-graph-up-arrow',
            'color': 'secondary'
        }
    ]
    return render_template('index.html', projects=projects, stats=[])

@main_bp.route('/api/server_stats')
def api_server_stats():
    from app.utils.metrics import get_server_stats
    stats_data = get_server_stats()
    return jsonify(stats_data)