MAX_UPLOAD_SIZE = 512 * 1024 * 1024
POSTER_MAX_SIZE = 20 * 1024 * 1024
CHUNK_SIZE = 8 * 1024 * 1024
UPLOAD_LIFETIME_HOURS = 24
UPLOAD_SLOTS = 4
UPLOAD_LEASE_SECONDS = 120

KEYWORDS = (
    '前端',
    '后端',
    '全栈',
    'Python',
    'JavaScript',
    'AI / 大模型',
    '数据分析',
    '硬件 / IoT',
    '移动开发',
    '游戏开发',
    'UI / UX',
    '产品设计',
    '项目管理',
    '演讲 / 路演',
    '视频制作',
    '商业策划',
    '新手愿学',
)

ALLOWED_EXTENSIONS = {
    'poster': {'.png', '.jpg', '.jpeg', '.webp'},
    'slides': {'.pdf', '.pptx'},
    'video': {'.mp4', '.webm'},
    'source': {'.zip', '.pdf'},
}
