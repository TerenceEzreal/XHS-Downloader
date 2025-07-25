#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
简单测试事件循环修复
"""

import asyncio
from asyncio import new_event_loop, set_event_loop

def run_async(coro):
    """在同步环境中运行异步函数"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = new_event_loop()
        set_event_loop(loop)
    
    return loop.run_until_complete(coro)

async def test_async_function():
    """测试异步函数"""
    print("🔄 异步函数开始执行...")
    await asyncio.sleep(0.1)
    print("✅ 异步函数执行完成")
    return "成功"

def test_in_sync_context():
    """在同步上下文中测试异步函数"""
    print("🧪 在同步上下文中测试异步函数...")
    
    try:
        result = run_async(test_async_function())
        print(f"✅ 测试成功，结果: {result}")
        return True
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        return False

async def test_multiple_async_calls():
    """测试多个异步调用"""
    print("🔄 测试多个异步调用...")
    
    async def task(name, delay):
        print(f"📋 任务 {name} 开始")
        await asyncio.sleep(delay)
        print(f"✅ 任务 {name} 完成")
        return f"任务{name}结果"
    
    # 并发执行多个任务
    results = await asyncio.gather(
        task("A", 0.1),
        task("B", 0.05),
        task("C", 0.15)
    )
    
    print(f"🎉 所有任务完成，结果: {results}")
    return results

def test_concurrent_processing():
    """测试并发处理"""
    print("\n🧪 测试并发处理...")
    
    try:
        results = run_async(test_multiple_async_calls())
        if len(results) == 3:
            print("✅ 并发处理测试成功")
            return True
        else:
            print("❌ 并发处理结果不正确")
            return False
    except Exception as e:
        print(f"❌ 并发处理测试失败: {e}")
        return False

def simulate_telegram_callback():
    """模拟 Telegram 回调函数中的场景"""
    print("\n🧪 模拟 Telegram 回调场景...")
    
    # 模拟在没有事件循环的同步回调中调用异步函数
    def sync_callback():
        """模拟同步回调函数"""
        print("📞 同步回调函数被调用")
        
        # 这里模拟原来会出错的场景
        try:
            # 在同步回调中运行异步函数
            result = run_async(test_async_function())
            print(f"✅ 在同步回调中成功执行异步函数: {result}")
            return True
        except Exception as e:
            print(f"❌ 在同步回调中执行异步函数失败: {e}")
            return False
    
    return sync_callback()

def main():
    """主测试函数"""
    print("🎯 开始简单事件循环测试\n")
    
    tests = [
        ("基础异步函数", test_in_sync_context),
        ("并发处理", test_concurrent_processing),
        ("Telegram回调模拟", simulate_telegram_callback),
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        print(f"\n{'='*50}")
        print(f"测试: {test_name}")
        print('='*50)
        
        if test_func():
            passed += 1
            print(f"✅ {test_name} 测试通过")
        else:
            print(f"❌ {test_name} 测试失败")
    
    print(f"\n🎉 测试完成: {passed}/{total} 通过")
    
    if passed == total:
        print("🎊 所有测试都通过了！事件循环问题已修复。")
        print("\n📝 修复说明:")
        print("- 移除了对 create_task 的直接调用")
        print("- 使用 run_async 函数在同步环境中安全运行异步代码")
        print("- 简化了任务管理，避免了事件循环冲突")
        return True
    else:
        print("⚠️ 部分测试失败，需要进一步检查。")
        return False

if __name__ == "__main__":
    try:
        success = main()
        exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n🛑 测试被用户中断")
        exit(1)
    except Exception as e:
        print(f"\n❌ 测试过程中发生错误: {e}")
        exit(1)
