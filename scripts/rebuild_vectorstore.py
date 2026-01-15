#!/usr/bin/env python3
"""
重建 RAG 向量存储缓存脚本
用于在更新 resources/docs 中的文档后重新生成 vectorstore_cache.pkl
也可以直接删除 resources/docs/vectorstore_cache.pkl 文件，然后重启 RAG MCP 服务，它会自动重建。

使用方法:
    python tests/rebuild_vectorstore.py
"""

import sys
import os

# 添加项目路径
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(project_root, 'src'))

from umamusume_agent.rag.rag import initialize_rag

def main():
    print("=" * 60)
    print("  RAG 向量存储重建工具".center(56))
    print("=" * 60)
    print()
    print("📂 文档目录: resources/docs/")
    print("🎯 缓存文件: resources/docs/vectorstore_cache.pkl")
    print()
    print("⚠️  注意: 此操作将删除现有缓存并重新构建向量数据库")
    print()
    
    # 确认操作
    try:
        confirm = input("是否继续? (y/N): ").strip().lower()
        if confirm not in ['y', 'yes']:
            print("❌ 操作已取消")
            return
    except KeyboardInterrupt:
        print("\n❌ 操作已取消")
        return
    
    print()
    print("🚀 开始重建向量存储...")
    print("-" * 60)
    
    try:
        # 强制重建向量存储
        initialize_rag(mode="auto", force_rebuild=True)
        
        print("-" * 60)
        print()
        print("✅ 向量存储重建完成!")
        print()
        print("📝 提示: 如果 RAG MCP 服务正在运行，请重启服务以加载新的向量存储")
        
    except Exception as e:
        print()
        print(f"❌ 重建失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()

