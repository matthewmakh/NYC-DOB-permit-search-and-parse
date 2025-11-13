#!/bin/bash
# Startup script for DOB Permit Dashboard

echo "🚀 Starting DOB Permit Dashboard..."
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "⚠️  Virtual environment not found. Creating one..."
    python3 -m venv venv
    echo "✅ Virtual environment created"
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install/update dependencies
echo "📦 Installing dependencies..."
pip install -q -r requirements.txt

# Check for .env file
if [ ! -f ".env" ]; then
    echo ""
    echo "⚠️  No .env file found!"
    echo "📝 Copy .env.example to .env and configure your database:"
    echo "   cp .env.example .env"
    echo ""
    read -p "Do you want to continue without .env? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo ""
echo "✅ Setup complete!"
echo "🌐 Starting Flask server..."
echo "📍 Dashboard will be available at: http://localhost:5000"
echo ""
echo "Press Ctrl+C to stop the server"
echo "─────────────────────────────────────────────"
echo ""

# Start Flask app
python app.py
