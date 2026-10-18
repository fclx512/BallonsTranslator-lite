from .base import TEXTDETECTORS, TextBlock, TextDetectorBase

# ysg_ctd 包装 ysgyolo：它保留独立配置段，但文档上复用基础检测器的文字检测
# 设置。新建配置段时从已保存的 ysgyolo 配置播种，而不是让模板默认值漏进去。
TEXTDETECTOR_PARAM_INHERITANCE = {"ysg_ctd": "ysgyolo"}

# 掩膜扩张尺寸不继承：ysgyolo 的矩形框 2px 就够，ysg_ctd 的字形掩膜需要
# 盖住文字描边，默认从 8px 起步。
TEXTDETECTOR_PARAM_INHERITANCE_EXCLUDE = {"ysg_ctd": {"mask dilate size"}}
