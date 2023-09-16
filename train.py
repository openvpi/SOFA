import torch
import torchaudio
import torchaudio.functional as F
import torchaudio.transforms as T
import pandas as pd
import numpy as np
import utils
import torch.nn as nn
import os
from torch.utils.data import DataLoader, Dataset
import torch.optim as optim
from torch.optim.lr_scheduler import OneCycleLR
from torch.utils.tensorboard import SummaryWriter
from itertools import cycle 
from utils import SinScheduler, GaussianRampUpScheduler
from tqdm import tqdm, trange
from inference import infer_once
import random
import torch
import numpy as np
from dataset import FullLabelDataset, collate_fn, NoLabelDataset
from model import FullModel, EMA
from dataloader import BinaryDataLoader

import yaml
from argparse import Namespace

with open('config.yaml', 'r') as file:
    config = yaml.safe_load(file)
config=utils.dict_to_namespace(config)

with open('vocab.yaml', 'r') as file:
    dict = yaml.safe_load(file)

import warnings
warnings.filterwarnings("ignore")

torch.manual_seed(config.random_seed)
np.random.seed(config.random_seed)
random.seed(config.random_seed)

if __name__ == '__main__':

    full_train_dataset = FullLabelDataset(name='train')
    full_train_dataloader = DataLoader(dataset=full_train_dataset, batch_size=config.batch_size_sup, shuffle=True,collate_fn=collate_fn)
    full_train_dataiter = cycle(full_train_dataloader)

    usp_dataset = NoLabelDataset(name='train')
    usp_dataloader = DataLoader(dataset=usp_dataset, batch_size=config.batch_size_usp, shuffle=True,collate_fn=collate_fn)
    usp_dataloader = cycle(usp_dataloader)

    usp_scheduler = GaussianRampUpScheduler(config.max_steps,0,config.max_steps)

    valid_dataset = FullLabelDataset(name='valid')
    valid_dataloader = DataLoader(dataset=valid_dataset, batch_size=config.batch_size_sup, shuffle=False,collate_fn=collate_fn)

    model=FullModel().to(config.device)
    # ema = EMA(model, 0.99)
    # ema.register()
    seg_loss_fn=nn.CrossEntropyLoss(label_smoothing=config.label_smoothing)
    EMD_loss_fn=utils.BinaryEMDLoss()
    BCE_loss_fn=nn.BCELoss()
    MSE_loss_fn=nn.MSELoss()

    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate,weight_decay=config.weight_decay)
    scheduler = OneCycleLR(optimizer, max_lr=config.learning_rate, total_steps=config.max_steps)

    progress_bar = tqdm(total=config.max_steps, ncols=80, bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]')

    model_name='model'
    writer = SummaryWriter()
    print('start training')
    for i in range(config.max_steps):
        model.train()
        optimizer.zero_grad()

        # full supervised training
        melspec,target,edge_target=next(full_train_dataiter)
        melspec,target,edge_target=\
        torch.tensor(melspec).to(config.device),\
        torch.tensor(target).to(config.device).long().squeeze(1),\
        torch.tensor(edge_target).to(config.device).float()
        h,seg,ctc,edge=model(melspec)
        # print(seg.shape,target.shape)
        seg_loss=seg_loss_fn(seg,target.squeeze(1))
        edge_loss=BCE_loss_fn(edge,edge_target)+EMD_loss_fn(edge,edge_target)
        loss=edge_loss+seg_loss

        writer.add_scalar('Accuracy/train', (seg.argmax(dim=1)==target).float().mean().item(), i)
        writer.add_scalar('Loss/train/sup/seq', seg_loss.item(), i)
        writer.add_scalar('Loss/train/sup/edge', edge_loss.item(), i)

        # semi supervised training
        if usp_scheduler()>0:
            # pass
            feature, feature_weak_aug, feature_strong_aug=next(usp_dataloader)
            feature, feature_weak_aug, feature_strong_aug=feature.to(config.device), feature_weak_aug.to(config.device), feature_strong_aug.to(config.device)
            h,seg,ctc,edge=model(feature)
            h_weak,seg_weak,ctc_weak,edge_weak=model(feature_weak_aug)
            h_strong,seg_strong,ctc_strong,edge_strong=model(feature_strong_aug)
            consistence_loss=(
                MSE_loss_fn(seg_weak,seg)+MSE_loss_fn(seg_strong,seg)+MSE_loss_fn(seg_strong,seg_weak)+\
                MSE_loss_fn(edge_weak,edge)+MSE_loss_fn(edge_strong,edge)+MSE_loss_fn(edge_strong,edge_weak)
            )

            writer.add_scalar('Loss/train/consistence', consistence_loss.item(), i)

            loss+=usp_scheduler()*consistence_loss

        loss.backward()
        optimizer.step()
        scheduler.step()
        usp_scheduler.step()
        # ema.update()

        writer.add_scalar('Loss/train/total', loss.item(), i)
        writer.add_scalar('learning_rate/total', optimizer.param_groups[0]['lr'], i)
        writer.add_scalar('learning_rate/usp', usp_scheduler(), i)
        progress_bar.set_description(f'tr_loss: {loss.item():.3f}')
        progress_bar.update()

        if i%config.val_interval==0:
            # pass
            # print('validating...')
            model.eval()
            # ema.apply_shadow()
            val_acc=[]
            val_loss_seg=[]
            val_loss_edge=[]
            with torch.no_grad():
                for melspec,target,edge_target in valid_dataloader:
                    melspec,target,edge_target=melspec.to(config.device),target.to(config.device).squeeze(1),edge_target.to(config.device)
                    h,seg,ctc,edge=model(melspec)
                    
                    val_acc.append((seg.argmax(dim=1)==target).float().mean().item())
                    val_loss_seg.append(seg_loss_fn(seg,target).item())
                    val_loss_edge.append(BCE_loss_fn(edge,edge_target)+EMD_loss_fn(edge,edge_target).item())
            
            # ema.restore()

            val_acc_total=torch.mean(torch.tensor(val_acc))
            val_loss_seg_total=torch.mean(torch.tensor(val_loss_seg))
            val_loss_edge_total=torch.mean(torch.tensor(val_loss_edge))
            writer.add_scalar('Accuracy/valid', val_acc_total, i)
            writer.add_scalar('Loss/valid/seg', val_loss_seg_total, i)
            writer.add_scalar('Loss/valid/edge', val_loss_edge_total, i)
        
        if i%config.test_interval==0:
            # pass
            print('testing...')
            model.eval()
            id=1

            for path, subdirs, files in os.walk(os.path.join('data','test')):
                for file in files:
                    if file=='transcriptions.csv':
                        trans=pd.read_csv(os.path.join(path,file))
                        trans['path'] = trans.apply(lambda x: os.path.join(path,'wavs', x['name']+'.wav'), axis=1)
                        
                        ph_confidence_total=[]
                        for idx in trange(len(trans)):
                            ph_seq_pred,ph_dur_pred,ph_confidence,plot1,plot2=infer_once(
                                trans.loc[idx,'path'],
                                trans.loc[idx,'ph_seq'].split(' '),
                                model,
                                return_plot=True)
                            
                            writer.add_figure(f'{id}/melseg', plot1, i)
                            writer.add_figure(f'{id}/probvec', plot2, i)
                            id+=1

                            ph_confidence_total.append(ph_confidence)
                        writer.add_scalar('Accuracy/test_confidence', np.mean(ph_confidence_total), i)
                        
        
        if i%config.save_ckpt_interval==0 and i != 0:
            # ema.apply_shadow()
            torch.save(model.state_dict(), f'ckpt/{model_name}_{i}.pth')
            # ema.restore()
            print(f'saved model at {i} steps, path: ckpt/{model_name}_{i}.pth')

    progress_bar.close()