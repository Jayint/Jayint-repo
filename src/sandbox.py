import docker
import time

class Sandbox:
    def __init__(self, base_image="python:3.10", workdir="/app", volumes=None):
        self.client = docker.from_env()
        self.base_image = base_image
        self.workdir = workdir
        self.volumes = volumes  # Mapping of {local_path: {'bind': container_path, 'mode': 'rw'}}
        self.current_image = base_image
        self.container = None
        self.last_success_image = None  # 记录上一次成功状态的镜像
        self._setup_initial_container()

    def _setup_initial_container(self):
        """Initializes the container from the base image."""
        print(f"Initializing container from {self.current_image}...")
        self.container = self.client.containers.run(
            self.current_image,
            detach=True,
            tty=True,
            working_dir=self.workdir,
            command="/bin/bash",
            volumes=self.volumes
        )
        # Ensure workdir exists
        self.container.exec_run(f"mkdir -p {self.workdir}")

    def execute(self, command):
        """
        Executes a bash command with rollback mechanism.
        Returns (success, output).
        """
        print(f"[Container ID: {self.container.short_id}]")
        print(f"Executing: {command}")
        
        # Execute the command
        exec_result = self.container.exec_run(
            ["/bin/bash", "-c", command],
            workdir=self.workdir
        )
        
        exit_code = exec_result.exit_code
        output = exec_result.output.decode('utf-8', errors='replace')
        
        # 判断是否为"信息性退出"（非真正错误）
        is_informational_exit = self._is_informational_exit(exit_code, output)
        
        if exit_code == 0 or is_informational_exit:
            # Success: 保存当前成功状态
            if is_informational_exit:
                print(f"Command exited with code {exit_code} (informational, not an error).")
            else:
                print("Command succeeded.")
            
            # 优化：只对会对环境产生影响的指令进行 commit
            if self._should_commit(command):
                # 只在成功时创建快照（用于可能的回滚）
                if self.last_success_image:
                    # 清理上一个成功快照，避免镜像堆积
                    try:
                        old_image = self.client.images.get(self.last_success_image)
                        self.client.images.remove(old_image.id, force=True)
                    except:
                        pass
                
                # 创建新的成功快照
                success_image = self.container.commit()
                self.last_success_image = success_image.id
                print(f"[Snapshot Created] {self.last_success_image[:12]}")
            else:
                print("[Skip Snapshot] Command is read-only or informational.")
            
            return True, output
        else:
            # Failure: 从上一次成功状态回滚
            print(f"Command failed (exit {exit_code}). Rolling back...")
            self.container.stop()
            self.container.remove()
            
            # 从上一次成功的镜像重启（如果存在）
            rollback_image = self.last_success_image if self.last_success_image else self.base_image
            self.container = self.client.containers.run(
                rollback_image,
                detach=True,
                tty=True,
                working_dir=self.workdir,
                command="/bin/bash",
                volumes=self.volumes
            )
            return False, output
    
    def _should_commit(self, command):
        """
        判断指令是否会对环境产生影响，从而决定是否需要 commit。
        """
        # 常见的不产生副作用的指令
        readonly_commands = [
            'ls', 'cat', 'pwd', 'echo', 'env', 'hostname', 'whoami', 
            'head', 'tail', 'grep', 'find', 'du', 'df', 'top', 'ps', 
            'date', 'which', 'type', 'file'
        ]
        
        # 获取指令的第一个单词
        first_word = command.strip().split()[0].lower() if command.strip() else ""
        
        # 如果指令在只读列表中，则不 commit
        if first_word in readonly_commands:
            return False
            
        # 默认需要 commit
        return True
    
    def _is_informational_exit(self, exit_code, output):
        """
        判断是否为信息性退出（如显示帮助信息），而非真正的错误。
        """
        # Exit code 1-2 通常是参数错误或显示帮助
        if exit_code not in [1, 2]:
            return False
        
        # 检查输出中是否包含帮助信息的特征
        help_indicators = [
            'Usage:',
            'usage:',
            '--help',
            'Options:',
            'Commands:',
            'positional arguments:',
            'optional arguments:'
        ]
        
        output_lower = output.lower()
        return any(indicator.lower() in output_lower for indicator in help_indicators)

    def get_container_info(self):
        """返回容器的详细信息，用于调试验证"""
        if self.container:
            return {
                'id': self.container.id,
                'short_id': self.container.short_id,
                'name': self.container.name,
                'status': self.container.status
            }
        return None
    
    def close(self, keep_alive=False):
        """关闭容器，可选择保持容器运行以供验证"""
        if self.container:
            if keep_alive:
                print(f"\n[Container Kept Alive] ID: {self.container.short_id}")
                print(f"To inspect: docker exec -it {self.container.short_id} /bin/bash")
                print(f"To stop later: docker stop {self.container.short_id}")
            else:
                try:
                    self.container.stop()
                    self.container.remove()
                    print("\n[Container Cleaned Up]")
                except:
                    pass
        
        # 清理最后的成功快照镜像
        if self.last_success_image:
            try:
                old_image = self.client.images.get(self.last_success_image)
                self.client.images.remove(old_image.id, force=True)
                print("[Snapshot Image Cleaned]")
            except:
                pass
        
        # 清理所有未使用的中间镜像
        try:
            pruned = self.client.images.prune(filters={'dangling': True})
            if pruned.get('ImagesDeleted'):
                print(f"[Pruned {len(pruned['ImagesDeleted'])} dangling images]")
        except:
            pass
