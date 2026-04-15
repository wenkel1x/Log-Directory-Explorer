from app import create_app

app = create_app()

if __name__ == '__main__':
    # 生产环境建议 host='0.0.0.0'
    app.run(host='0.0.0.0', port=5000, debug=False)
    #app.run(host='0.0.0.0', port=5000, debug=True)
