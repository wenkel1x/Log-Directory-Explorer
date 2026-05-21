// static/js/app_core.js

/**
 * 🛠️ 多租户身份控制器 (数据库物理隔离锁)
 */
const AppTenantManager = {
    // 获取当前租户 Key
    getProjectKey: function() {
        const urlParams = new URLSearchParams(window.location.search);
        let key = urlParams.get('project_key');

        if (!key) {
            // 如果刷新页面丢了，从浏览器本地会话里捞
            key = sessionStorage.getItem('CURRENT_PROJECT_KEY');
        } else {
            // 如果 URL 里有，随时同步刷新缓存
            sessionStorage.setItem('CURRENT_PROJECT_KEY', key);
        }
        return key;
    },

    // 安全检查：防止无证空指针访问
    enforceAuth: function() {
        const key = this.getProjectKey();
        if (!key && window.location.pathname !== '/') {
            console.warn("Access Denied: Missing Project Token. Redirecting...");
            window.location.href = '/';
        }
    },

    // 自动为普通的链接或者 AJAX 附加租户令牌
    bindNavbarLinks: function() {
        const key = this.getProjectKey();
        if (!key) return;

        // 全局动态拦截导航栏点击，避免硬编码丢失状态
        $(document).ready(function() {
            // 假设你的 navbar 链接分别带有 class 或特殊属性
            $('a.nav-link-home').attr('href', '/?project_key=' + key);
            $('a.nav-link-search').attr('href', '/search?project_key=' + key);
            $('a.nav-link-tree').attr('href', '/tree?project_key=' + key);
        });
    }
};

// 页面加载时立即启动安全看门狗和导航拦截
AppTenantManager.enforceAuth();
AppTenantManager.bindNavbarLinks();

// 导出全局快捷变量供各自页面的 JS 使用
const CURRENT_PROJECT_KEY = AppTenantManager.getProjectKey();