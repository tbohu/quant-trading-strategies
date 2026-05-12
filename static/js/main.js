// 全局工具函数

function scoreColor(s) {
  if (!s && s !== 0) return 'text-muted';
  if (s >= 85) return 'text-success';
  if (s >= 75) return 'text-warning';
  return 'text-danger';
}

// 格式化万元
function fmt_w(v) {
  if (v == null) return '-';
  return (v / 10000).toFixed(0) + 'w';
}

// 初始化 Bootstrap tooltips
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
    new bootstrap.Tooltip(el);
  });
});
