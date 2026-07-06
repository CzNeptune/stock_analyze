def normalize_code(raw):
    """
    智能识别股票代码前缀，支持直接输入数字代码。
    规则：
      A股6位数字: 60/68/69开头 → sh(沪市), 00/30/20开头 → sz(深市), 43/83/87/88/92开头 → bj(北交所)
      港股: 5位或4位数字 → hk
      美股: 纯字母 → us
      已带前缀(sh/sz/hk/us/bj): 原样返回
    """
    code = raw.strip().lower()

    # 已带前缀
    for prefix in ("sh", "sz", "bj", "hk", "us"):
        if code.startswith(prefix) and len(code) > len(prefix):
            return code

    # 纯字母 → 美股
    if code.isalpha():
        return "us" + code.upper()

    # 纯数字 → 按位数和开头判断市场
    if code.isdigit():
        n = len(code)
        if n == 6:
            # A股
            head = code[:2]
            if head in ("60", "68", "69"):
                return "sh" + code
            elif head in ("00", "30", "20", "02", "31"):
                return "sz" + code
            elif head in ("43", "83", "87", "88", "92"):
                return "bj" + code
            else:
                # 兜底：6开头沪市，其余深市
                return ("sh" if code[0] == "6" else "sz") + code
        elif n == 5:
            # 港股5位
            return "hk" + code
        elif n == 4:
            # 港股4位（部分老代码）
            return "hk" + code
        elif n == 1:
            # 港股1位代码（极少，如0001长和实际是5位）
            return "hk" + code.zfill(5)

    # 无法识别，原样返回（让 westock-data 自己报错）
    return code
