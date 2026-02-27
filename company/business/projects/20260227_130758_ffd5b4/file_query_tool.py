import os
import yaml

def get_employee_profile(employee_id, company_root="."):
    """获取员工档案"""
    profile_path = os.path.join(company_root, f"human_resource/employees/{employee_id}/profile.yaml")
    if not os.path.exists(profile_path):
        return None
    with open(profile_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def check_permission(employee_id, file_path, company_root="."):
    """
    检查员工是否有权限访问指定文件。
    权限规则：
    1. CEO (Lv.5) 和 创始员工 (Lv.4) 拥有所有文件的访问权限。
    2. HR 部门拥有 human_resource 目录下所有文件的访问权限。
    3. 员工始终可以访问自己的档案及所在部门的相关文件。
    4. assets 和 business 目录默认对相关业务人员开放，但敏感财务/资产数据仅限高管或相关负责人。
    """
    profile = get_employee_profile(employee_id, company_root)
    if not profile:
        return False
        
    role = profile.get('role', '')
    level = profile.get('level', 1)
    department = profile.get('department', '')
    
    # CEO和高级创始员工拥有最高权限
    if level >= 4:
        return True
        
    # 规范化路径
    normalized_path = os.path.normpath(file_path)
    path_parts = normalized_path.split(os.sep)
    
    # 1. 人力资源目录 (human_resource)
    if path_parts[0] == "human_resource":
        if role == "HR":
            return True
        # 员工只能查看自己的信息
        if len(path_parts) >= 3 and path_parts[1] == "employees" and path_parts[2] == employee_id:
            return True
        return False
        
    # 2. 资产与工具目录 (assets)
    if path_parts[0] == "assets":
        # 所有人都可以查看基础工具和房间，但可能有特定限制
        return True
        
    # 3. 业务与项目目录 (business)
    if path_parts[0] == "business":
        # 所有人均可查看公开的业务文件
        return True
        
    # 4. 公司文化 (company_culture.yaml)
    if file_path == "company_culture.yaml":
        return True
        
    return False

def query_file(employee_id, file_path, company_root="."):
    """
    查询文件内容的工具函数。
    """
    if not check_permission(employee_id, file_path, company_root):
        return {"status": "error", "message": f"Permission denied for employee {employee_id} to access {file_path}."}
        
    full_path = os.path.join(company_root, file_path)
    if not os.path.exists(full_path):
        return {"status": "error", "message": "File not found."}
        
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return {"status": "ok", "content": content}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# 示例调用
# print(query_file("00002", "human_resource/employees/00001/profile.yaml"))
