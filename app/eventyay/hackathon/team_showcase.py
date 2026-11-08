"""Writing examples for the team directory; these are never participant records."""

from dataclasses import dataclass


@dataclass(frozen=True)
class WritingExample:
    label: str
    title: str
    body: str
    invitation: str
    tip: str
    action: str
    kind: str = ''
    tags: tuple[str, ...] = ()


SEEKER_EXAMPLES = (
    WritingExample(
        label='个人介绍 · 开发方向',
        title='我能做前端，想和后端伙伴搭一个校园工具',
        body='会使用 React，做过课程项目，能负责页面和交互。希望从一个真实的校园小问题出发。',
        invitation='想遇到：愿意一起定义问题的后端或 AI 方向伙伴。',
        tip='写清能贡献的技能、感兴趣的问题，以及希望伙伴补充什么。',
        action='写我的找队友介绍',
        tags=('前端', 'React', '校园生活'),
    ),
    WritingExample(
        label='个人介绍 · 产品与设计',
        title='我擅长访谈和原型，想找人一起验证一个想法',
        body='喜欢观察同学使用校园服务时的困扰，能做用户访谈、流程梳理和 Figma 原型。',
        invitation='想遇到：愿意先验证需求，再一起实现 Demo 的伙伴。',
        tip='不必堆满技术名词；把你能负责的具体工作说清楚。',
        action='写我的找队友介绍',
        tags=('用户访谈', '原型设计', 'Figma'),
    ),
    WritingExample(
        label='个人介绍 · 一起学习',
        title='第一次参加黑客松，愿意从测试和演示开始',
        body='了解 Python 基础，想在真实项目中学习协作。可以整理资料、测试流程、准备演示。',
        invitation='想遇到：愿意交流、一起把小功能做完整的伙伴。',
        tip='已有能力与想学的内容分开写，新手也可以具体地介绍自己。',
        action='写我的找队友介绍',
        tags=('Python', '新手愿学', '测试与质量'),
    ),
)

COMMUNITY_EXAMPLES = (
    WritingExample(
        kind='experience',
        label='真实经历 / 痛点',
        title='为了找一间空教室，我来回跑了三栋楼',
        body='想分享一次找自习空间的经历：信息分散，到现场才发现教室临时被占用。',
        invitation='你也遇到过吗？想听听大家现在怎么解决。',
        tip='描述具体场景和经历，再邀请有相似体验的人交流。',
        action='分享一段真实经历',
    ),
    WritingExample(
        kind='question',
        label='开放问题',
        title='怎样让第一次参加校园活动的人更容易找到同伴？',
        body='想讨论的不是一个完整产品，而是新同学独自参加活动时，最需要哪一步帮助。',
        invitation='欢迎补充经历、反例，或一起聊十分钟。',
        tip='用一个明确的问题开场，说明你期待哪种回应。',
        action='提出一个开放问题',
    ),
    WritingExample(
        kind='help',
        label='具体求助',
        title='页面已经跑通，想找人一起排查接口报错',
        body='能复现问题，也整理了报错与尝试过的方法。希望有人帮忙看一下请求和返回数据。',
        invitation='想请熟悉 Python 接口的伙伴一起排查二十分钟。',
        tip='说清卡在哪里、试过什么、需要什么帮助；不要贴密码或密钥。',
        action='发布一条具体求助',
    ),
)

POOL_EXAMPLES = (
    WritingExample(
        kind='idea',
        label='灵感 / 脑洞',
        title='一个想法：把分散的校园通知整理成待办清单',
        body='目前只有问题和使用场景：重要通知散在不同群里，很容易错过需要行动的时间。',
        invitation='想找人一起判断：这个问题值得做吗？最小功能是什么？',
        tip='还没有代码也能发布；说明问题、面向的人和想验证的假设。',
        action='分享一个灵感',
    ),
    WritingExample(
        kind='showcase',
        label='展示 / 开发日志',
        title='第一版原型完成：课程提醒终于能发出来了',
        body='已经实现课程录入和提醒，下一步准备测试跨周课程。可以附上截图、演示或代码链接。',
        invitation='想邀请伙伴体验流程，看看哪里不清楚、哪里容易出错。',
        tip='写清已完成什么、还缺什么，并给出可查看的作品链接。',
        action='分享作品 / 开发日志',
    ),
    WritingExample(
        kind='co_create',
        label='微型共创',
        title='一起用二十分钟，把一个报错提示改得更好懂',
        body='有一个能运行的表单，想和一位伙伴一起梳理错误状态，改一版提示，再互相试用。',
        invitation='欢迎擅长写文案、做交互或想练习测试的伙伴一起试试。',
        tip='把任务缩到一次能完成的小目标，说明需要的能力和预计投入。',
        action='发起一次微型共创',
    ),
)
