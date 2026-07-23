#!/usr/bin/env python3
"""生成局域网丰碑竞争报告（v2 - 含insight创建者归属）"""

import json
import os
from collections import defaultdict


def get_creator(data):
    """智能获取创建者"""
    meta = data.get('metadata', {})
    creator = meta.get('creator', '')
    if creator and creator != '?':
        return creator
    # fallback: insight的ai_id
    ai_id = data.get('ai_id', '')
    if ai_id and ai_id != '?':
        return ai_id
    return 'unknown'


def generate_report():
    candidates_dir = '/vol2/1000/AI专用/丰碑网络/candidates'

    # 读取所有丰碑
    monuments = []
    for filename in sorted(os.listdir(candidates_dir)):
        if filename.endswith('.json'):
            filepath = os.path.join(candidates_dir, filename)
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if 'monument_score' in data:
                monuments.append({
                    'title': data.get('title', data.get('ai_id', 'unknown')),
                    'monument_score': data['monument_score'],
                    'time_binding': data.get('time_binding', 0),
                    'transferability': data.get('transferability', 0),
                    'abstraction_level': data.get('abstraction_level', 0),
                    'creator': get_creator(data),
                    'file': filename
                })

    # 按创建者分组
    by_creator = defaultdict(list)
    for m in monuments:
        by_creator[m['creator']].append(m)

    # 计算竞争得分
    competition = []
    for creator, ms in by_creator.items():
        scores = [m['monument_score'] for m in ms]
        competition.append({
            'creator': creator,
            'count': len(ms),
            'max_score': max(scores),
            'avg_score': sum(scores) / len(scores),
            'promoted': len([s for s in scores if s >= 0.8])
        })

    # 排序
    competition.sort(key=lambda x: x['avg_score'], reverse=True)
    monuments.sort(key=lambda x: x['monument_score'], reverse=True)

    # 输出报告
    print('# 局域网丰碑竞争报告\n')
    print(f'总丰碑数: {len(monuments)} 座\n')

    print('## AI 竞争排行榜\n')
    print('| 排名 | 创建者 | 丰碑数 | 最高分 | 平均分 | 晋升数 |')
    print('|------|--------|--------|--------|--------|--------|')
    for i, c in enumerate(competition, 1):
        print(f'| {i} | {str(c["creator"])[:24]:24s} | {c["count"]:6d} | {c["max_score"]:.3f} | {c["avg_score"]:.3f} | {c["promoted"]:6d} |')

    print('\n## 丰碑总排行榜（前20）\n')
    print('| 排名 | 标题 | 评分 | 创建者 |')
    print('|------|------|------|--------|')
    for i, m in enumerate(monuments[:20], 1):
        print(f'| {i:3d} | {str(m["title"])[:40]:40s} | {m["monument_score"]:.3f} | {str(m["creator"])[:20]:20s} |')

    print(f'\n## 统计\n')
    print(f'- 参赛AI: {len(competition)} 个')
    print(f'- 总丰碑: {len(monuments)} 座')
    print(f'- 晋升候选(≥0.8): {len([m for m in monuments if m["monument_score"] >= 0.8])} 座')
    print(f'- 平均评分: {sum([m["monument_score"] for m in monuments]) / len(monuments):.3f}')

    print('\n## 各AI详细结果\n')
    for c in competition:
        print(f'### {c["creator"][:40]}\n')
        print(f'- 丰碑数: {c["count"]} | 最高分: {c["max_score"]:.3f} | 平均分: {c["avg_score"]:.3f} | 晋升数: {c["promoted"]}')
        creator_ms = [m for m in monuments if m['creator'] == c['creator']]
        creator_ms.sort(key=lambda x: x['monument_score'], reverse=True)
        for m in creator_ms:
            print(f'  - {m["monument_score"]:.3f}  {str(m["title"])[:60]}')
        print()

    # 补充：未评分的文件
    unscored = []
    for filename in sorted(os.listdir(candidates_dir)):
        if filename.endswith('.json'):
            with open(os.path.join(candidates_dir, filename)) as f:
                data = json.load(f)
            if 'monument_score' not in data:
                unscored.append(filename)
    if unscored:
        print(f'## 未评分文件（{len(unscored)} 个）\n')
        for f in unscored:
            print(f'- {f}')


if __name__ == '__main__':
    generate_report()
