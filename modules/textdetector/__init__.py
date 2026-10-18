from .base import TEXTDETECTORS, TextBlock, TextDetectorBase

# ysg_ctd 包装 ysgyolo：它保留独立配置段，但文档上复用基础检测器的文字检测
# 设置。新建配置段时从已保存的 ysgyolo 配置播种，而不是让模板默认值漏进去。
TEXTDETECTOR_PARAM_INHERITANCE = {"ysg_ctd": "ysgyolo"}
