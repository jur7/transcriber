# transcriber




# Local Docker Container Testing Guide

## Prerequisites
- Docker Desktop running on Mac
- Project code ready
- Port 5001 available

## Steps

### 1. Navigate to Project Directory
```bash
cd /Users/arnould/Documents/transcriber-project
```

### 2. Build Image for Local Testing
```bash
docker build -t transcriber-project-transcriber:latest .
```

### 3. Run Container Locally
```bash
docker run -d \
  --name transcriber-local \
  -p 5001:5001 \
  -v $(pwd)/transcriptions.db:/app/transcriptions.db \
  transcriber-project-transcriber:latest
```
Explanation of flags:
- `-d`: Run in detached mode (background)
- `--name`: Give container a name for easy reference
- `-p 5001:5001`: Map port 5001 on your Mac to port 5001 in container
- `-v $(pwd)/transcriptions.db:/app/transcriptions.db`: Mount database file

### 4. Verify Container is Running
```bash
docker ps
```

### 5. Check Container Logs (if needed)
```bash
docker logs transcriber-local
```

### 6. Access the Application
- Open browser and go to: `http://localhost:5001`

### 7. Stop and Clean Up (when done testing)
```bash
# Stop container
docker stop transcriber-local

# Remove container
docker rm transcriber-local
```

## Troubleshooting
- If port 5001 is in use:
  - Change port mapping (e.g., `-p 5002:5001`)
  - Or stop other containers using that port
- If container exits immediately:
  - Check logs: `docker logs transcriber-local`
- If volume mount fails:
  - Ensure transcriptions.db exists (or will be created by the app)
  - Check Docker Desktop file sharing settings

## Notes
- Keep Docker Desktop running during testing
- Container name 'transcriber-local' can be changed to any name you prefer
- The database file will persist between container restarts due to the volume mount

Alternative: Using Docker Compose
```bash
# Start container
docker-compose up -d

# View logs
docker-compose logs

# Stop container
docker-compose down
```






# Docker Image Update Guide

## Prerequisites
- Docker Desktop running
- Project code updated

## Steps

### 1. Navigate to Project Directory
```bash
cd /Users/arnould/Documents/transcriber-project
```

### 2. Login to Docker Hub
```bash
docker login
```

### 3. Build the Image
For AMD64 architecture (for cloud deployment):
```bash
docker buildx build --platform linux/amd64 -t arnoulddw/transcriber-app:latest .
```

### 4. Tag Images
Use this automated script to tag with both latest and today's date:
```bash
# Create date-time tag automatically (format: YYYY-MM-DD-HHMM)
DATETIME_TAG=$(date +%Y-%m-%d-%H%M)
docker tag arnoulddw/transcriber-app:latest arnoulddw/transcriber-app:$DATETIME_TAG
```

### 5. Push Both Tags to Docker Hub
```bash
# Push both the datetime-tagged and latest versions
docker push arnoulddw/transcriber-app:$DATETIME_TAG
docker push arnoulddw/transcriber-app:latest
```


### 6. Verify Images
```bash
docker images | grep arnoulddw/transcriber-app
```

## Common Issues & Solutions
- If push fails: Check internet connection and docker login status
- If build fails: Check Dockerfile syntax and project dependencies
- If tag fails: Ensure the source image exists

## Notes
- The `$DATE_TAG` variable automatically gets today's date in YYYY-MM-DD format
- Both tagged versions (latest and date) will be available on Docker Hub
- Always push both tags to maintain version history
- The `latest` tag will always point to your most recent version

---

Key differences from your version:
1. You don't need to manually tag `transcriber-project-transcriber:latest` because your build command already tags it as `arnoulddw/transcriber-app:latest`
2. Added automatic date tagging using `$(date +%Y-%m-%d)`
3. Included pushing both tags explicitly
4. Added troubleshooting section
5. Structured in a more readable format

Save this somewhere accessible (like a README.md in your project) for future reference!













# Git Quick Reference Guide

## Initial Setup (One-time only)
1. Create GitHub repository online
2. Navigate to project folder in Terminal:
```bash
cd /Users/arnould/Documents/transcriber-project
```
3. Initialize Git:
```bash
git init
```

## Authentication Setup (One-time only)
1. Go to GitHub.com → Your Profile → Settings → Developer Settings → Personal Access Tokens → Tokens (classic)
2. Generate new token (classic)
3. Select 'repo' permissions
4. Copy token and save it somewhere safe
5. Use this token as password when GitHub asks for authentication

## Regular Workflow (Every time you make changes)
1. Check status of changes:
```bash
git status
```

2. Add changes to staging:
```bash
git add .               # Add all changes
# OR
git add filename.py    
```

3. Commit changes:
```bash
git commit -m "Describe what you changed"
```

4. Push to GitHub:
```bash
git push
```

## Login Credentials
- Username: arnoulddw
- Password: Use Personal Access Token (NOT GitHub password)

## Useful Commands
```bash
git pull               # Get latest changes from GitHub
git log               # View commit history
git diff              # See changes before committing
```

## If You Get Stuck
- Check if you're in the right directory
- Verify you're using the correct Personal Access Token
- Make sure you've added and committed before pushing
- If authentication fails, generate a new token

## Repository URL
https://github.com/arnoulddw/transcriber.git

Remember: Never commit sensitive information or API keys directly to Git!