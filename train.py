import argparse
import json
import time
from time import localtime, strftime
import test
from models import *
from shutil import copyfile
from utils.datasets import JointDataset, collate_fn
from utils.utils import *
from utils.log import logger
from torchvision.transforms import transforms as T
import warnings
from utils.preprocess import globalEqualization 

def train(
        cfg,
        data_cfg,
        weights_from="",
        weights_to="",
        save_every=10,
        img_size=(1088, 608),
        resume=False,
        epochs=100,
        batch_size=16,
        accumulated_batches=1,
        freeze_backbone=False,
        opt=None,
        workers = 8,
        usewandb=True,
        timme=""
):
    # The function starts

    final_result = []    
    weights_to = osp.join(weights_to, 'run_' + timme)
    mkdir_if_missing(weights_to)
    if resume:
        latest_resume = osp.join(weights_from, 'latest.pt')

    torch.backends.cudnn.benchmark = True  # unsuitable for multiscale

    # Configure run
    f = open(data_cfg)
    data_config = json.load(f)
    trainset_paths = data_config['train']
    dataset_root = data_config['root']
    f.close()

    transforms = T.Compose([
        T.ToTensor()])
    # Get dataloader
    dataset = JointDataset(dataset_root, trainset_paths, img_size, augment=True, transforms=transforms)
    dataloader = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True,
                                             num_workers=workers, pin_memory=True, drop_last=True, collate_fn=collate_fn)
    # Initialize model
    model = Swin_JDE(cfg, dataset.nID)

    if usewandb:
        wandb.watch(model)

    cutoff = -1  # backbone reaches to cutoff layer
    start_epoch = 0
    if resume:
        checkpoint = torch.load(latest_resume, map_location='cpu')

        # Load weights to resume from
        model.load_state_dict(checkpoint['model'])
        model.cuda().train()

        # Set optimizer
        optimizer = torch.optim.AdamW(filter(lambda x: x.requires_grad, model.parameters()), lr=opt.lr)

        start_epoch = checkpoint['epoch'] + 1
        if checkpoint['optimizer'] is not None:
            optimizer.load_state_dict(checkpoint['optimizer'])
            optimizer.param_groups[0]['lr'] = opt.lr
        del checkpoint  # current, saved

    elif opt.pretrain:
        # Initialize model with backbone (optional)
        if cfg.endswith('swin_b.cfg'):
            load_swin_weights(model, osp.join(weights_from, 'swinb.pth'))
        elif cfg.endswith('swin_s_mod_6.cfg'):
            load_swin_weights(model, osp.join(weights_from, 'swin.pth'))
        elif cfg.endswith('swin_s_mod_5.cfg'):
            load_swin_weights(model, osp.join(weights_from, 'best_swin-jde-crowdhuman.pt'))
        else:
            load_darknet_weights(model, weights_from)

        model.cuda().train()

        # Set optimizer
        optimizer = torch.optim.SGD(filter(lambda x: x.requires_grad, model.parameters()), lr=opt.lr, momentum=.9, weight_decay=1e-4)
        # optimizer = torch.optim.AdamW(filter(lambda x: x.requires_grad, model.parameters()), lr=opt.lr, weight_decay=5e-3)
    else:
        print ('Optimizer Adam')
        model.cuda().train()
        optimizer = torch.optim.AdamW(filter(lambda x: x.requires_grad, model.parameters()), lr=opt.lr)
        # optimizer = torch.optim.SGD(filter(lambda x: x.requires_grad, model.parameters()), lr=opt.lr, momentum=0.9, weight_decay=5e-3)

    model = torch.nn.DataParallel(model)
    # Set scheduler
    # scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=30, gamma=0.1)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min', patience=15, factor=0.75)
    # scheduler = torch.optim.lr_scheduler.MultiStepLR(optimizer,
    #                                                  milestones=[int(opt.epochs - 9), int(opt.epochs - 3)],
    #                                                  gamma=0.1)
    # scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, epochs)

    # An important trick for detection: freeze bn during fine-tuning
    if not opt.unfreeze_bn:
        for i, (name, p) in enumerate(model.named_parameters()):
            p.requires_grad = False if 'batch_norm' in name else True
    
    best_mAP, best_Recall = -1, -1
    mAP, R, P = 0,0,0  # use to save the best model
    mota, motp, idf1, num_switches = 0, 0, 0, 0
    best_mota = -100
    # model_info(model)
    t0 = time.time()
    for epoch in range(epochs - start_epoch + 1):
        epoch += start_epoch
        logger.info(('%8s%12s' + '%10s' * 6) % (
            'Epoch', 'Batch', 'box', 'conf', 'id', 'total', 'nTargets', 'time'))

        # Freeze darknet53.conv.74 for first epoch
        # if freeze_backbone and (epoch < 2):
        #     for i, (name, p) in enumerate(model.named_parameters()):
        #         if int(name.split('.')[2]) < cutoff:  # if layer < 75
        #             p.requires_grad = False if (epoch == 0) else True

        ui = -1
        rloss = defaultdict(float)  # running loss
        optimizer.zero_grad()
        for i, (imgs, targets, _, _, targets_len) in enumerate(dataloader):
            # print(imgs.shape)
        #     for x in imgs:
        #         time.sleep(2)
        #         transform = T.ToPILImage()
        #         print(imgs)

        #         img = transform(x)
        #         img.show()
        #     continue
        # exit(0)
        
            if sum([len(x) for x in targets]) < 1:  # if no targets continue
                continue

            # SGD burn-in
            # burnin = min(1000, len(dataloader))
            # if (epoch == 0) & (i <= burnin):
            #     lr = opt.lr * (i / burnin) ** 4
            #     for g in optimizer.param_groups:
            #         g['lr'] = lr

            # Compute loss, compute gradient, update parameters
            loss, components = model(imgs.cuda(), targets.cuda(), targets_len.cuda())
            components = torch.mean(components.view(-1, 5), dim=0)
            loss = torch.mean(loss)
            loss.backward()

            # accumulate gradient for x batches before optimizing
            if ((i + 1) % accumulated_batches == 0) or (i == len(dataloader) - 1):
                optimizer.step()
                optimizer.zero_grad()

            # Running epoch-means of tracked metrics
            ui += 1

            for ii, key in enumerate(model.module.loss_names):
                rloss[key] = (rloss[key] * ui + components[ii]) / (ui + 1)

            # rloss indicates running loss values with mean updated at every epoch
            s = ('%8s%12s' + '%10.3g' * 6) % (
                '%g/%g' % (epoch, epochs - 1),
                '%g/%g' % (i, len(dataloader) - 1),
                rloss['box'], rloss['conf'],
                rloss['id'], rloss['loss'],
                rloss['nT'], time.time() - t0)
            t0 = time.time()
            if i % opt.print_interval == 0:
                logger.info(s)
            

        # Save latest checkpoint
        checkpoint = {'epoch': epoch,
                      'model': model.module.state_dict(),
                      'optimizer': optimizer.state_dict()}

        copyfile(cfg, weights_to + '/yolo3.cfg')
        copyfile(data_cfg, weights_to + '/ccmcpe.json')

        latest = osp.join(weights_to, 'latest.pt')
        torch.save(checkpoint, latest)
        if epoch % opt.test_interval == 0 and epoch != 0:
            # making the checkpoint lite
            torch.save(checkpoint, osp.join(weights_to, "weights_epoch_" + str(epoch) + ".pt"))

        # Calculate mAP
        if epoch % opt.test_interval == 0:
            with torch.no_grad():
                mAP, R, P = test.test(cfg, data_cfg, weights=latest, batch_size=batch_size, print_interval=40)
                # test.test_emb(cfg, data_cfg, weights=latest, batch_size=batch_size, print_interval=40)
                if mAP > best_mAP:
                    torch.save(checkpoint, osp.join(weights_to, 'best_mAP.pt'))
                    best_mAP = mAP

                if R > best_Recall:
                    torch.save(checkpoint, osp.join(weights_to, 'best_recall.pt'))
                    best_Recall = R

                final_result.append([epoch, mAP, R, P])
                best_mAP = max(best_mAP, mAP)


        
        if usewandb:
            wandb.log({'mAP': mAP, 'Recall': R, 'Precision': P,
                        'epoch': epoch, 'box_loss': rloss['box'], 'conf_loss': rloss['conf'], 'id_loss': rloss['id'],'loss': rloss['loss'],
                        'nT_loss': rloss['nT'], "lr": optimizer.param_groups[0]["lr"], "epoch_time": time.time()-t0})

        # Call scheduler.step() after opimizer.step() with pytorch > 1.1.0
        scheduler.step(epoch)

    
    print('Epoch,   mAP,   R,   P:')
    for row in final_result:
        print(row[0], row[1][0], row[2][0], row[3][0])

    print(f"best mAP:  {best_mAP}")
    print(f"best Recall:  {best_Recall}")
    print(f"best MOTA:  {best_mota}")



if __name__ == '__main__':
    warnings.filterwarnings('ignore')
    parser = argparse.ArgumentParser()
    parser.add_argument('--epochs', type=int, default=30, help='number of epochs')
    parser.add_argument('--batch-size', type=int, default=32, help='size of each image batch')
    parser.add_argument('--accumulated-batches', type=int, default=1, help='number of batches before optimizer step')
    parser.add_argument('--cfg', type=str, default='cfg/yolov3.cfg', help='cfg file path')
    parser.add_argument('--weights-from', type=str, default='weights/',
                        help='Path for getting the trained model for resuming training (Should only be used with '
                             '--resume)')
    parser.add_argument('--weights-to', type=str, default='weights/',
                        help='Store the trained weights after resuming training session. It will create a new folder '
                             'with timestamp in the given path')
    parser.add_argument('--save-model-after', type=int, default=10,
                        help='Save a checkpoint of model at given interval of epochs')
    parser.add_argument('--data-cfg', type=str, default='cfg/ccmcpe.json', help='coco.data file path')
    parser.add_argument('--img-size', type=int, default=[1088, 608], nargs='+', help='pixels')
    parser.add_argument('--resume', action='store_true', help='resume training flag')
    parser.add_argument('--print-interval', type=int, default=40, help='print interval')
    parser.add_argument('--test-interval', type=int, default=9, help='test interval')
    parser.add_argument('--lr', type=float, default=3e-4, help='init lr')
    parser.add_argument('--unfreeze-bn', action='store_true', help='unfreeze bn')
    parser.add_argument('--num-worker', type=int, default=8, help='Data loader')
    parser.add_argument('--nowandb', action='store_true', help='disable wandb')
    parser.add_argument('--pretrain', action='store_true', help='use prertained weights')
    parser.add_argument('--watermark', type=str, default='Swin_JDE', help='watermark for wandb')
    parser.add_argument('--iou-thres', type=float, default=0.5, help='iou threshold required to qualify as detected')
    parser.add_argument('--conf-thres', type=float, default=0.5, help='object confidence threshold')
    parser.add_argument('--nms-thres', type=float, default=0.4, help='iou threshold for non-maximum suppression')
    parser.add_argument('--min-box-area', type=float, default=200, help='filter out tiny boxes')
    parser.add_argument('--track-buffer', type=int, default=30, help='tracking buffer')
    parser.add_argument('--save-images', action='store_true', help='save tracking results (image)')
    parser.add_argument('--save-videos', action='store_true', help='save tracking results (video)')
    parser.add_argument('--train-mot17', action='store_true', help='tracking buffer')
    parser.add_argument('--val-mot17', action='store_true', help='tracking buffer')
    parser.add_argument('--weights', type=str, default='weights/latest.pt', help='path to weights file')
    opt = parser.parse_args()
    timme = strftime("%Y_%m_%d_%H_%M_%S", localtime())

    usewandb = opt.nowandb == False
    if usewandb:
        import wandb
        watermar = opt.watermark
        wandb.init(project="SWIN-JDE", name=watermar + '_'+ timme)
        wandb.config.update(opt)

    init_seeds()


    train(
        opt.cfg,
        opt.data_cfg,
        weights_from=opt.weights_from,
        weights_to=opt.weights_to,
        save_every=opt.save_model_after,
        img_size=opt.img_size,
        resume=opt.resume,
        epochs=opt.epochs,
        batch_size=opt.batch_size,
        accumulated_batches=opt.accumulated_batches,
        opt=opt,
        workers=opt.num_worker,
        usewandb=usewandb,
        timme = timme
    )
