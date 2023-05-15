import os

sample_path = 'Datasets/MIX/images/train/'  #ADL-Rundle-6/img1/000001.jpg'

train = ['MOT17-02-SDP', 'MOT17-04-SDP', 'MOT17-05-SDP', 'MOT17-09-SDP', 'MOT17-10-SDP', 'MOT17-11-SDP', 'MOT17-13-SDP']
test =  ['MOT17-02-SDP', 'MOT17-04-SDP', 'MOT17-05-SDP', 'MOT17-09-SDP', 'MOT17-10-SDP', 'MOT17-11-SDP', 'MOT17-13-SDP']
train_files = []

l = []
for t in train:
    path = sample_path + t + '/img1/'
    files = os.listdir(os.getcwd() + '/' + path)
    files.sort()
    s = []
    l.append(int(len(files)*.7))
    for f in files[0:l[-1]]:
        s.append(path + f)
    train_files += s

with open('data/mot17.train', 'w+') as f:
    for items in train_files:
        f.write('%s\n' %items)

f.close()

test_files = []
sample_path = 'Datasets/MIX/images/train/'

i=0
for t in test:
    print (t)
    path = sample_path + t + '/img1/'
    files = os.listdir(os.getcwd() + '/' + path)
    files.sort()
    s = []
    for f in files[l[i]:]:
        s.append(path + f)
    test_files += s

print(len(test_files))
with open('data/mot17.test', 'w+') as f:
    for items in test_files:
        f.write('%s\n' %items)

f.close()