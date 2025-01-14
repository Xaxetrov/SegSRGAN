"""
  This software is governed by the CeCILL-B license under French law and
  abiding by the rules of distribution of free software.  You can  use,
  modify and/ or redistribute the software under the terms of the CeCILL-B
  license as circulated by CEA, CNRS and INRIA at the following URL
  "http://www.cecill.info".
  As a counterpart to the access to the source code and  rights to copy,
  modify and redistribute granted by the license, users are provided only
  with a limited warranty  and the software's author,  the holder of the
  economic rights,  and the successive licensors  have only  limited
  liability.
  In this respect, the user's attention is drawn to the risks associated
  with loading,  using,  modifying and/or developing or reproducing the
  software by the user in light of its specific status of free software,
  that may mean  that it is complicated to manipulate,  and  that  also
  therefore means  that it is reserved for developers  and  experienced
  professionals having in-depth computer knowledge. Users are therefore
  encouraged to load and test the software's suitability as regards their
  requirements in conditions enabling the security of their systems and/or
  data to be ensured and,  more generally, to use and operate it in the
  same conditions as regards security.
  The fact that you are presently reading this means that you have had
  knowledge of the CeCILL-B license and that you accept its terms.
"""
import numpy as np
import argparse
import os
import sys
sys.path.insert(0, './utils')
from SegSRGAN import SegSRGAN
from patches import create_patch_from_df_HR
import pandas as pd
from ast import literal_eval as make_tuple
import shutil
import time
     

class SegSRGAN_train(object):
    def __init__(self,
                 BasePath,
                 contrast_max,
                 percent_val_max,
                 list_res_max,
                 Trainingcsv, 
                 multi_gpu,
                 patch=64, 
                 FirstDiscriminatorKernel = 32, FirstGeneratorKernel = 16,
                 lamb_rec = 1, lamb_adv = 0.001, lamb_gp = 10, 
                 lr_DisModel = 0.0001, lr_GenModel = 0.0001,u_net_gen=False,
                 is_conditional=False,
                 is_residual=True):
        
        self.SegSRGAN = SegSRGAN(ImageRow=patch, ImageColumn=patch, ImageDepth=patch, 
                                 FirstDiscriminatorKernel = FirstDiscriminatorKernel, 
                                 FirstGeneratorKernel = FirstGeneratorKernel,
                                 lamb_rec = lamb_rec, lamb_adv = lamb_adv, lamb_gp = lamb_gp, 
                                 lr_DisModel = lr_DisModel, lr_GenModel = lr_GenModel,u_net_gen=u_net_gen,multi_gpu=multi_gpu,is_conditional=is_conditional)
        self.generator = self.SegSRGAN.generator()
        self.Trainingcsv = Trainingcsv
        self.DiscriminatorModel, self.DiscriminatorModel_multi_gpu = self.SegSRGAN.discriminator_model()
        self.GeneratorModel, self.GeneratorModel_multi_gpu = self.SegSRGAN.generator_model()
        self.BasePath = BasePath
        self.contrast_max = contrast_max
        self.percent_val_max = percent_val_max
        self.list_res_max = list_res_max
        self.multi_gpu=multi_gpu
        self.is_conditional=is_conditional
        self.is_residual=is_residual
        
        print("initalisation finised")
        
    def train(self,
              snapshot_folder,
              dice_file,
              mse_file, 
              folder_training_data,
              TrainingEpoch=200, BatchSize=16, SnapshotEpoch=1, InitializeEpoch=1, NumCritic=5, 
              resuming = None):
        
        #snapshot_prefix='weights/SegSRGAN_epoch'
        print("train begin")
        snapshot_prefix = snapshot_folder+"/SegSRGAN_epoch"
        
        # boolean to print only one time 'the number of patch not in one epoche (mod batchsize)'
        never_print = True
        if os.path.exists(snapshot_folder) is False:
            os.makedirs(snapshot_folder)
        
        # Initialization Parameters
        real = -np.ones([BatchSize, 1], dtype=np.float32)
        fake = -real
        dummy = np.zeros([BatchSize, 1], dtype=np.float32)   
        
        # Data processing
        #TrainingSet = ProcessingTrainingSet(self.TrainingText,BatchSize, InputName='data', LabelName = 'label')
        
        data=pd.read_csv(self.Trainingcsv)
        
        data["HR_image"] = self.BasePath+data["HR_image"]
        data["Label_image"] = self.BasePath+data["Label_image"]

        
        data_train =data[data['Base']=="Train"]
        data_test = data[data['Base']=="Test"]
        
        # Resuming
        if InitializeEpoch==1:
            iteration = 0
            if resuming is None:
                print ("Training from scratch")
            else:
                print("Training from the pretrained model (names of layers must be identical): ", resuming)
                self.GeneratorModel.load_weights(resuming, by_name=True)
        
        elif InitializeEpoch <1:
            raise AssertionError('Resumming needs a positive epoch')
        else:
            if resuming is None:
                raise AssertionError('We need pretrained weights')
            else:
                print('Continue training from : ', resuming)
                self.GeneratorModel.load_weights(resuming, by_name=True)
#                iteration = (InitializeEpoch-1)*iterationPerEpoch
        # patch test creation :
                
        t1=time.time()
        
        test_contrast_list=np.linspace(1-self.contrast_max,1+self.contrast_max,data_test.shape[0])
        
        
        # list_res[0] = lower bound and list_res[1] = borne supp
        # list_res[0][0] = lower bound for the first coordinate
        
        lin_res_x = np.linspace(self.list_res_max[0][0],self.list_res_max[1][0],data_test.shape[0])
        lin_res_y = np.linspace(self.list_res_max[0][1],self.list_res_max[1][1],data_test.shape[0])
        lin_res_z = np.linspace(self.list_res_max[0][2],self.list_res_max[1][2],data_test.shape[0])
        
          
        res_test = [(lin_res_x[i],
                     lin_res_y[i],
                     lin_res_z[i]) for i in range(data_test.shape[0])]
                
        test_path_save_npy,test_Path_Datas_mini_batch , test_Labels_mini_batch, test_remaining_patch = create_patch_from_df_HR(df = data_test,
                                                                                                         per_cent_val_max = self.percent_val_max,
                                                                                                         contrast_list = test_contrast_list,
                                                                                                         list_res = res_test,
                                                                                                         order=3,
                                                                                                         normalisation=False,
                                                                                                         thresholdvalue=0,
                                                                                                         PatchSize=64,
                                                                                                         batch_size = 1, #1 to keep all data
                                                                                                         path_save_npy=folder_training_data+"/test_mini_batch",
                                                                                                         stride=20,is_conditional=self.is_conditional)
        
        t2=time.time()
        
        print("time for making test npy :"+str(t2-t1))   

        df_dice = pd.DataFrame(index=np.arange(InitializeEpoch,TrainingEpoch+1),columns=["Dice"])
        df_MSE = pd.DataFrame(index=np.arange(InitializeEpoch,TrainingEpoch+1),columns=["MSE"])
        
        # Training phase
        for EpochIndex in range(InitializeEpoch,TrainingEpoch+1):  
    
            train_contrast_list=np.random.uniform(1-self.contrast_max,1+self.contrast_max,data_train.shape[0])
            
            
            res_train = [(np.random.uniform(self.list_res_max[0][0],self.list_res_max[1][0]),
                          np.random.uniform(self.list_res_max[0][1],self.list_res_max[1][1]),
                          np.random.uniform(self.list_res_max[0][2],self.list_res_max[1][2])) for i in range(data_train.shape[0])]
            
            t1=time.time()
        
            train_path_save_npy,train_Path_Datas_mini_batch ,train_Labels_mini_batch, train_remaining_patch = create_patch_from_df_HR  (df = data_train,
                                                                                                               per_cent_val_max = self.percent_val_max,
                                                                                                               contrast_list = train_contrast_list,
                                                                                                               list_res = res_train,
                                                                                                               order=3,
                                                                                                               normalisation=False,
                                                                                                               thresholdvalue=0,
                                                                                                               PatchSize=64,
                                                                                                               batch_size = BatchSize,
                                                                                                               path_save_npy=folder_training_data+"/train_mini_batch",
                                                                                                               stride=20,
                                                                                                               is_conditional=self.is_conditional)
            iterationPerEpoch = len(train_Path_Datas_mini_batch)
            
            t2=time.time()
        
            print("time for making train npy :"+str(t2-t1))

            if never_print: 
                                                                                                 
                print("At each epoch "+str(train_remaining_patch)+" patches will not be in the training data for this epoch")
                never_print = False                                          
                                                      
            
            print("Processing epoch : " + str(EpochIndex))
            for iters in range(0,iterationPerEpoch):
                
                iteration += 1
                
                # Training discriminator
                for cidx in range(NumCritic):
                    
                    t1=time.time()

                    # Loading data randomly
                    randomNumber = int(np.random.randint(0,iterationPerEpoch,1))
                    
                    train_input = np.load(train_Path_Datas_mini_batch[randomNumber])[:,0,:,:,:][:,np.newaxis,:,:,:]# select 0 coordoniate and add one axis at the same place
                    
                    train_output = np.load(train_Labels_mini_batch[randomNumber])                    
                    
                    if self.is_conditional :
                        
                        train_res = np.load(train_Path_Datas_mini_batch[randomNumber])[:,1,:,:,:][:,np.newaxis,:,:,:]
                         
                        # Generating fake and interpolation images
                        fake_images = self.GeneratorModel_multi_gpu.predict([train_input,train_res])[1]
                        epsilon = np.random.uniform(0, 1, size=(BatchSize,2,1,1,1))
                        interpolation = epsilon*train_output + (1-epsilon)*fake_images
                        # Training
                        dis_loss = self.DiscriminatorModel_multi_gpu.train_on_batch([train_output,fake_images,interpolation,train_res],
                                                                           [real,fake,dummy])
                    else :
                        
                        # Generating fake and interpolation images
                        fake_images = self.GeneratorModel_multi_gpu.predict(train_input)[1]
                        epsilon = np.random.uniform(0, 1, size=(BatchSize,2,1,1,1))
                        interpolation = epsilon*train_output + (1-epsilon)*fake_images
                        # Training
                        dis_loss = self.DiscriminatorModel_multi_gpu.train_on_batch([train_output,fake_images,interpolation],
                                                                           [real,fake,dummy])
                         
                    t2=time.time()
        
                    print("time for one uptade of discriminator :"+str(t2-t1))
                    print("Update "+ str(cidx) + ": [D loss : "+str(dis_loss)+"]") 
                    
                # Training generator
                # Loading data        
                
                t1=time.time()
                
                train_input_gen = np.load(train_Path_Datas_mini_batch[iters])[:,0,:,:,:][:,np.newaxis,:,:,:]
                train_output_gen = np.load(train_Labels_mini_batch[iters])
                
                if self.is_conditional:
                    
                    train_res_gen = np.load(train_Path_Datas_mini_batch[iters])[:,1,:,:,:][:,np.newaxis,:,:,:]
                    # Training                                      
                    gen_loss = self.GeneratorModel_multi_gpu.train_on_batch([train_input_gen,train_res_gen], 
                                                                   [real,train_output_gen])
                else : 
                    # Training                                      
                    gen_loss = self.GeneratorModel_multi_gpu.train_on_batch([train_input_gen], 
                                                                   [real,train_output_gen])
                    
                                                                       
                print("Iter "+ str(iteration) + " [A loss : " + str(gen_loss) + "]")
                
                t2=time.time()
                
                print("time for one uptade of generator :"+str(t2-t1))
                
            if (EpochIndex)%SnapshotEpoch==0:
                # Save weights:
                self.GeneratorModel.save_weights(snapshot_prefix + '_' + str(EpochIndex))
                print ("Snapshot :" + snapshot_prefix + '_' + str(EpochIndex))
                
            MSE_list= []
            VP = []
            Pos_pred = []
            Pos_label = []
            
            t1 = time.time()
            
            
            for test_iter in range(len(test_Labels_mini_batch)):

                TestLabels = np.load(test_Labels_mini_batch[test_iter])
                TestDatas = np.load(test_Path_Datas_mini_batch[test_iter])[:,0,:,:,:][:,np.newaxis,:,:,:]
                
                if self.is_conditional : 
                    
                    TestRes = np.load(test_Path_Datas_mini_batch[test_iter])[:,1,:,:,:][:,np.newaxis,:,:,:]
                    
                    pred = self.generator.predict([TestDatas,TestRes])
                    
                else : 
                    
                    pred = self.generator.predict([TestDatas])
                    
                

                pred[:,0,:,:,:][pred[:,0,:,:,:]<0]=0            
            
                MSE_list.append(np.sum((pred[:,0,:,:,:]-TestLabels[:,0,:,:,:])**2))
                
                VP.append(np.sum((pred[:,1,:,:,:]>0.5)&(TestLabels[:,1,:,:,:]==1)))
                
                Pos_pred.append(np.sum(pred[:,1,:,:,:]>0.5))
                
                Pos_label.append(np.sum(TestLabels[:,1,:,:,:]))
                
            t2 = time.time()
            
            print("Evaluation on test data time : " + str(t2-t1))
                
            gen_weights = np.array(self.GeneratorModel.get_weights())
            gen_weights_multi = np.array(self.GeneratorModel_multi_gpu.get_weights())
            
            weights_idem =True
            
            for i in range(len(gen_weights)) :
                
                idem =  np.array_equal(gen_weights[i],gen_weights_multi[i])
                
                weights_idem = weights_idem & idem 
                
            if weights_idem : 
                
                print("Model multi_gpu and base Model have the same weights")
                
            else :
                print("Model multi_gpu and base Model haven't the same weights")
                
            
            Dice = (2 * np.sum(VP))/(np.sum(Pos_pred)+np.sum(Pos_label))
            
            MSE = np.sum(MSE_list)/(BatchSize**3*len(MSE_list)) 
            
            print("Iter "+ str(EpochIndex) + " [Test Dice : " + str(Dice) + "]") 
            
            print("Iter "+ str(EpochIndex) + " [Test MSE : " + str(MSE) + "]")  
            
            df_dice.loc[EpochIndex,"Dice"]=Dice
            df_MSE.loc[EpochIndex,"MSE"]=MSE
            
            df_dice.to_csv(dice_file)
            df_MSE.to_csv(mse_file)
            
            shutil.rmtree(folder_training_data+"/train_mini_batch")
        
        shutil.rmtree(folder_training_data+"/test_mini_batch")
    
            
            
                        

if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('-begining_path', '--basepath', help='path to concatenate with relative path contains in csv file ', type=str, required = True) # path qui apres concatenation des path contenu dans le fichier csv amene au fichier.
    parser.add_argument('-n', '--newlowres', type=float, action='append',help='upper and lower bounds between which the low resolution of each image at each epoch will be choosen randomly' ,nargs="+",required=True)
    parser.add_argument('-contrast_max', '--contrast_max', help='Ex : 0.3 : NN trained on contrast between power 0.3 and 1.3 of initial image (default=0.5)', type=float,default=0.5)
    parser.add_argument('-percent_val_max', '--percent_val_max', help='NN trained on image on which we add gaussian noise with sigma equal this % of val_max', type=float,default=0.03)
    parser.add_argument('-csv', '--csv', help='.csv contining relative path for testing and training base', type=str, required = True) # tous les path se tranvant dans le fichier sont relatif a begining_path, collone HR_image : path HR Label_image : path Label	mask : path mask Base : "Train" ou "Test",
    parser.add_argument('-sf', '--snapshot_folder', help='Folder name for saving spanshot weights', type=str, required = True)
    parser.add_argument('-e', '--epoch', help='Number of training epochs (default=200)', type=int, default=200)
    parser.add_argument('-b', '--batchsize', help='Number of batch (default=16)', type=int, default=16)
    parser.add_argument('-s', '--snapshot', help='Snapshot Epoch (default=1)', type=int, default=1)
    parser.add_argument('-i', '--initepoch', help='Init Epoch (default=1)', type=int, default=1)
    parser.add_argument('-w', '--weights', help='Name of the pretrained HDF5 weight file (default: None)', type=str, default=None)
    parser.add_argument('--kernelgen', help='Number of filters of the first layer of generator (default=16)', type=int, default=16)
    parser.add_argument('--kerneldis', help='Number of filters of the first layer of discriminator (default=32)', type=int, default=32)
    parser.add_argument('--lrgen', help='Learning rate of generator (default=0.0001)', type=int, default=0.0001)
    parser.add_argument('--lrdis', help='Learning rate of discriminator (default=0.0001)', type=int, default=0.0001)
    parser.add_argument('--lambrec', help='Lambda of reconstruction loss (default=1)', type=int, default=1)
    parser.add_argument('--lambadv', help='Lambda of adversarial loss (default=0.001)', type=int, default=0.001)
    parser.add_argument('--lambgp', help='Lambda of gradien penalty loss (default=10)', type=int, default=10)
    parser.add_argument('--numcritic', help='Number of training time for discriminator (default=5) ', type=int, default=5)
    parser.add_argument('-dice','--dice_file', help='Dice path for save dice a the end of each epoche', type=str,required = True)
    parser.add_argument('-mse','--mse_file', help='MSE path for save dice a the end of each epoche', type=str,required = True)
    parser.add_argument('-u_net','--u_net_generator', help='Either the generator take u-net architecture (like u-net) or not. Value in {True,False} default : False', type=str,default="False")
    parser.add_argument('-folder_training_data','--folder_training_data', help="folder in which data organized by batch will be save during training (this folder will be created)", type=str,required =True)
    parser.add_argument('-multi_gpu','--multi_gpu', help="Train using all gpu available if some exist ? Value in {True,False} default : True", type=str,default="True")
    parser.add_argument('-is_conditional','--is_conditional', help="Should a conditionnal GAN be train ? Value in {True,False} default : False", type=str,default="False")
    parser.add_argument('-is_residual','--is_residual', help="Should a residual GAN be train (sum of pred and image for SR estimation) ? Value in {True,False} default : True", type=str,default="True")
    args = parser.parse_args()
    
    
    u_net = (args.u_net_generator=="True") #Transform str to boolean
    
    multi_gpu = (args.multi_gpu=="True")
    
    is_residual = (args.is_residual=="True")
    
    is_conditional = (args.is_conditional=="True")
    
    print("percent val max :"+str(args.percent_val_max))
    
    print("u_net = "+str(u_net))
    
    print("is_conditional = "+str(is_conditional))
    
    print("is_residual = "+str(is_residual))
    
    
    list_res_max = args.newlowres
    
    
    for i in range(len(list_res_max)):
        
        if len(list_res_max[i])!=3:
            raise AssertionError('Not support this resolution !')
            
    print("Initial resolution given "+str(list_res_max))
                
            
    if len(list_res_max)==1:
        
        list_res_max.extend(list_res_max)
        
    print("the low resolution of images will be choosen randomly between "+str(list_res_max[0])+" and "+str(list_res_max[1]))
        
        

    SegSRGAN_train = SegSRGAN_train(Trainingcsv = args.csv,  
                                    contrast_max=args.contrast_max,
                                    percent_val_max=args.percent_val_max,
                                    FirstDiscriminatorKernel = args.kerneldis, FirstGeneratorKernel = args.kernelgen,
                                    lamb_rec = args.lambrec, lamb_adv = args.lambadv, lamb_gp = args.lambgp, 
                                    lr_DisModel = args.lrdis, lr_GenModel = args.lrgen,BasePath=args.basepath,list_res_max=list_res_max,u_net_gen=u_net,multi_gpu=multi_gpu,is_conditional=is_conditional,is_residual=is_residual)
                                    
    SegSRGAN_train.train(TrainingEpoch=args.epoch, BatchSize=args.batchsize, 
                         SnapshotEpoch=args.snapshot, InitializeEpoch = args.initepoch,
                         NumCritic = args.numcritic,
                         resuming = args.weights,
                         dice_file=args.dice_file,
                         mse_file=args.mse_file,
                         snapshot_folder=args.snapshot_folder,
                         folder_training_data = args.folder_training_data
                         )
                         
                         