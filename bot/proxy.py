import requests

# BASE_REMOTE_ADDR = 'http://10.2.108.169:6000'
BASE_REMOTE_ADDR = 'http://192.168.1.192:6000'
# BASE_REMOTE_ADDR = 'http://192.168.1.211:6000'
# BASE_REMOTE_ADDR = 'http://192.168.68.51:6000'


def get(url):
    resp = requests.get(url)
    if resp.status_code == 200:
        return resp.json()
    else:
        return {"code": -1}


def get_stream(url):
    resp = requests.get(url)
    if resp.status_code == 200:
        return resp.content
    else:
        return b''


def post(url, payload):
    try:
        resp = requests.post(url, json=payload)
    except Exception as e:
        print(f'requests.post failed! payload={payload}')
    if resp.status_code == 200:
        return resp.json()
    else:
        return {"code": -1}


def click(x, y, times=1):
    # url = 'http://192.168.1.211:5001/query'
    url = 'http://10.2.108.169:5000/click'
    req = dict(
        x=x,
        y=y,
        times=times,
    )
    resp = post(url, req)
    resp_code = resp.get('code', -1)
    if resp_code != 0:
        print('unexpected error')
        return

    data = resp.get('data', {})

    print(f'response: {data}')
    return


def bid(price: str):
    if not price.isdecimal():
        return
    if int(price) <= 0:
        return

    # url = 'http://192.168.1.211:5001/query'
    url = 'http://10.2.108.169:5000/bid'
    req = dict(
        price=price,
    )
    resp = post(url, req)
    resp_code = resp.get('code', -1)
    if resp_code != 0:
        print('unexpected error')
        return

    data = resp.get('data', {})

    print(f'response: {data}')
    return


def send_remote_command(command, base_url=BASE_REMOTE_ADDR):
    url = f'{base_url}/command'
    print(f'proxy.send_remote_command, command={command}')
    print(f'send_remote_command, command={command}')
    req = dict(
        command=command,
    )
    resp = post(url, req)
    if not resp.get('success', False):
        print(f'unexpected error: {resp}')
        return resp

    print(f'response: {resp}')
    return resp


def sync_file(filename):
    # url = 'http://192.168.1.211:5001/query'
    url = f'{BASE_REMOTE_ADDR}/sync?filename={filename}'

    resp = requests.get(url)
    if resp.status_code == 200:
        return resp.content
    return b''


def list_files(directory):
    # url = 'http://192.168.1.211:5001/query'
    url = f'{BASE_REMOTE_ADDR}/list?directory={directory}'

    resp = get(url)
    if resp.get('code', -1) != 0:
        print('unexpected error')
        return []

    return resp.get('files', [])