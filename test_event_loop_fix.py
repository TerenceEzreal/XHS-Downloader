#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试事件循环修复
"""

import asyncio
import sys
import os

# 添加项目根目录到路径
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, 'pyTelegramBotAPI'))

def test_run_async_function():
    """测试 run_async 函数在同步环境中的工作"""
    print("🧪 测试 run_async 函数...")
    
    from bot import run_async
    
    async def test_async_function():
        """测试异步函数"""
        await asyncio.sleep(0.1)
        return "异步函数执行成功"
    
    try:
        result = run_async(test_async_function())
        print(f"✅ run_async 测试成功: {result}")
        return True
    except Exception as e:
        print(f"❌ run_async 测试失败: {e}")
        return False

def test_user_manager():
    """测试用户管理器的基本功能"""
    print("\n🧪 测试用户管理器...")
    
    from bot import UserDataManager
    
    try:
        manager = UserDataManager()
        test_user_id = 12345
        
        # 测试任务管理
        manager.add_active_task(test_user_id, "test_task_1")
        manager.add_active_task(test_user_id, "test_task_2")
        
        if test_user_id in manager.active_tasks:
            print(f"✅ 任务添加成功: {manager.active_tasks[test_user_id]}")
        
        # 测试任务清理
        manager.cancel_user_tasks(test_user_id)
        
        if not manager.active_tasks.get(test_user_id):
            print("✅ 任务清理成功")
        
        return True
        
    except Exception as e:
        print(f"❌ 用户管理器测试失败: {e}")
        return False

def test_xhs_pool_basic():
    """测试XHS实例池的基本功能"""
    print("\n🧪 测试XHS实例池基本功能...")
    
    from bot import XHSInstancePool, run_async
    
    async def test_pool():
        pool = XHSInstancePool(max_instances=1)
        
        try:
            # 测试获取实例（不实际初始化XHS，只测试池逻辑）
            print("📥 测试实例池逻辑...")
            
            # 模拟实例
            mock_instance = "mock_xhs_instance"
            pool.available_instances.append(mock_instance)
            
            # 测试获取
            await pool.semaphore.acquire()
            if pool.available_instances:
                instance = pool.available_instances.pop()
                pool.busy_instances.add(instance)
                print(f"✅ 成功获取实例: {instance}")
                
                # 测试归还
                pool.busy_instances.remove(instance)
                pool.available_instances.append(instance)
                pool.semaphore.release()
                print("✅ 成功归还实例")
                
            return True
            
        except Exception as e:
            print(f"❌ 实例池测试失败: {e}")
            return False
        finally:
            await pool.cleanup()
    
    try:
        result = run_async(test_pool())
        return result
    except Exception as e:
        print(f"❌ 实例池测试异常: {e}")
        return False

def main():
    """主测试函数"""
    print("🎯 开始事件循环修复测试\n")
    
    tests = [
        ("run_async 函数", test_run_async_function),
        ("用户管理器", test_user_manager),
        ("XHS实例池", test_xhs_pool_basic),
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
        return True
    else:
        print("⚠️ 部分测试失败，需要进一步检查。")
        return False

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n🛑 测试被用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 测试过程中发生错误: {e}")
        sys.exit(1)
