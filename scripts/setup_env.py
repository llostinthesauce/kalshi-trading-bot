
import os
import shutil

def setup_env():
    """
    Sets up the .env file from env.template if it doesn't exist.
    """
    env_file = ".env"
    template_file = "env.template"

    if os.path.exists(env_file):
        print(f"✅ {env_file} already exists.")
    else:
        if os.path.exists(template_file):
            shutil.copy(template_file, env_file)
            print(f"✅ Created {env_file} from {template_file}.")
        else:
            print(f"❌ {template_file} not found. Please ensure you are in the project root.")
            return

    print("\n⚠️  IMPORTANT: Please update .env with your actual API keys.")
    print(f"   Open {env_file} and fill in the values.")

if __name__ == "__main__":
    setup_env()
