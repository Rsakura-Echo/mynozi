"""editdistance 纯 Python 兼容实现。

当系统的 C 扩展版 editdistance 无法编译安装时（如 Windows Python 3.13），
用此纯 Python 版本替代。API 兼容 editdistance.eval()。
"""


def eval(a: str, b: str) -> int:
    """计算两个序列之间的 Levenshtein 编辑距离。

    O(n*m) DP，O(min(n,m)) 空间。
    """
    if not a:
        return len(b)
    if not b:
        return len(a)

    if len(a) > len(b):
        a, b = b, a

    n, m = len(a), len(b)
    prev = list(range(m + 1))

    for i in range(1, n + 1):
        curr = [i]
        ca = a[i - 1]
        for j in range(1, m + 1):
            cb = b[j - 1]
            curr.append(min(
                prev[j] + 1,
                curr[j - 1] + 1,
                prev[j - 1] + (ca != cb),
            ))
        prev = curr

    return prev[-1]
