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
        label='开发方向',
        title='前端开发，寻找后端 / AI 伙伴',
        body='做过 React 课程项目，可以负责页面与交互，想一起做个校园工具。',
        invitation='希望一起定义问题，把最小可用版本做出来。',
        tip='写清能贡献的技能、感兴趣的问题，以及希望伙伴补充什么。',
        action='写我的介绍',
        tags=('前端', 'React', '校园生活'),
    ),
    WritingExample(
        label='产品与设计',
        title='擅长产品与设计，一起验证想法',
        body='能做用户访谈、流程梳理和 Figma 原型，想从同学的真实需求开始。',
        invitation='寻找愿意先验证需求，再一起实现 Demo 的伙伴。',
        tip='不必堆满技术名词；把你能负责的具体工作说清楚。',
        action='写我的介绍',
        tags=('用户访谈', '原型设计', 'Figma'),
    ),
    WritingExample(
        label='新手参与',
        title='第一次黑客松，从测试和演示开始',
        body='了解 Python 基础，可以整理资料、测试流程、准备演示。',
        invitation='想和愿意交流的伙伴，一起把小功能做完整。',
        tip='已有能力与想学的内容分开写，新手也可以具体地介绍自己。',
        action='写我的介绍',
        tags=('Python', '新手愿学', '测试与质量'),
    ),
)

COMMUNITY_EXAMPLES = (
    WritingExample(
        kind='experience',
        label='真实经历 / 痛点',
        title='找自习教室，来回跑了三栋楼',
        body='教室信息散在不同地方，到现场才发现被临时占用。想聊聊找自习空间的经历。',
        invitation='你也遇到过吗？想听听大家现在怎么解决。',
        tip='描述具体场景和经历，再邀请有相似体验的人交流。',
        action='分享一段真实经历',
    ),
    WritingExample(
        kind='question',
        label='开放问题',
        title='第一次参加活动，怎样找到同伴？',
        body='新同学独自参加活动时，哪一步最需要帮助？还没有产品方案，想先听听真实经历。',
        invitation='欢迎补充经历、反例，或一起聊十分钟。',
        tip='用一个明确的问题开场，说明你期待哪种回应。',
        action='提出一个开放问题',
    ),
    WritingExample(
        kind='help',
        label='具体求助',
        title='接口报错，想找人一起排查',
        body='页面已跑通，接口报错能稳定复现。已整理报错和尝试过的方法，想一起看看请求。',
        invitation='想请熟悉 Python 接口的伙伴一起排查二十分钟。',
        tip='说清卡在哪里、试过什么、需要什么帮助；不要贴密码或密钥。',
        action='发布一条具体求助',
    ),
)

POOL_EXAMPLES = (
    WritingExample(
        kind='idea',
        label='灵感 / 脑洞',
        title='把校园通知，整理成待办清单',
        body='重要通知散在不同群里，很容易错过截止时间。想先验证自动整理待办是否有用。',
        invitation='想找人一起判断：这个问题值得做吗？最小功能是什么？',
        tip='还没有代码也能发布；说明问题、面向的人和想验证的假设。',
        action='分享一个灵感',
    ),
    WritingExample(
        kind='showcase',
        label='展示 / 开发日志',
        title='原型进展：课程提醒跑通了',
        body='课程录入和提醒已实现，下一步测试跨周课程。可以在发布时附上演示或代码链接。',
        invitation='想邀请伙伴体验流程，看看哪里不清楚、哪里容易出错。',
        tip='写清已完成什么、还缺什么，并给出可查看的作品链接。',
        action='分享作品 / 开发日志',
    ),
    WritingExample(
        kind='co_create',
        label='微型共创',
        title='20 分钟，一起改好一条报错提示',
        body='有一个能运行的表单，想一起梳理错误状态、改一版提示，再互相试用。',
        invitation='欢迎擅长写文案、做交互或想练习测试的伙伴一起试试。',
        tip='把任务缩到一次能完成的小目标，说明需要的能力和预计投入。',
        action='发起一次微型共创',
    ),
)
