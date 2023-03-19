import os

sample_path = 'Datasets/MIX/images/train/'  #ADL-Rundle-6/img1/000001.jpg'

train = ['KITTI-13', 'MOT17-09-SDP', 'MOT17-05-SDP', 'TUD-Campus', 'TUD-Stadtmitte', 'MOT17-11-SDP', 'MOT17-04-SDP']
test = ['KITTI-17', 'MOT17-10-SDP', 'ETH-Sunnyday', 'PETS09-S2L1']

train_files = []

for t in train:
    path = sample_path + t + '/img1/'
    files = os.listdir(os.getcwd() + '/' + path)
    files.sort()
    train_files += files

with open('data/MIX.train', 'w+') as f:
    for items in train_files:
        it = path + items
        f.write('%s\n' %it)

f.close()

test_files = []

for t in test:
    path = sample_path + t + '/img1/'
    files = os.listdir(os.getcwd() + '/' + path)
    files.sort()
    test_files += files

with open('data/MIX.test', 'w+') as f:
    for items in test_files:
        it = path + items
        f.write('%s\n' %it)

f.close()