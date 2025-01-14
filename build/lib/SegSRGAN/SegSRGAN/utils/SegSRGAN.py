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
from functools import partial

from keras.models import Model
from keras.layers import Input, LeakyReLU, Reshape
from keras.layers import Conv3D, Add, UpSampling3D, Activation, Concatenate
from keras.optimizers import Adam
from keras.initializers import lecun_normal
import keras.backend as K

gen_initializer = lecun_normal()

import sys

sys.path.insert(0, './utils')
from layers import wasserstein_loss, ReflectPadding3D, gradient_penalty_loss, InstanceNormalization3D, \
    activation_SegSRGAN, charbonnier_loss
from Adam_lr_mult import LR_Adam
from keras import losses
from keras.utils import multi_gpu_model
from tensorflow.python.client import device_lib


def get_available_gpus():
    local_device_protos = device_lib.list_local_devices()
    return [x.name for x in local_device_protos if x.device_type == 'GPU']


def resnet_blocks(input_res, kernel, name):
    in_res_1 = ReflectPadding3D(padding=1)(input_res)
    out_res_1 = Conv3D(kernel, 3, strides=1, kernel_initializer=gen_initializer,
                       use_bias=False,
                       name=name + '_conv_a',
                       data_format='channels_first')(in_res_1)
    out_res_1 = InstanceNormalization3D(name=name + '_isnorm_a')(out_res_1)
    out_res_1 = Activation('relu')(out_res_1)

    in_res_2 = ReflectPadding3D(padding=1)(out_res_1)
    out_res_2 = Conv3D(kernel, 3, strides=1, kernel_initializer=gen_initializer,
                       use_bias=False,
                       name=name + '_conv_b',
                       data_format='channels_first')(in_res_2)
    out_res_2 = InstanceNormalization3D(name=name + '_isnorm_b')(out_res_2)

    out_res = Add()([out_res_2, input_res])
    return out_res


class SegSRGAN(object):
    def __init__(self, ImageRow=64, ImageColumn=64, ImageDepth=64,
                 FirstDiscriminatorKernel=32, FirstGeneratorKernel=16,
                 lamb_rec=1, lamb_adv=0.001, lamb_gp=10,
                 lr_DisModel=0.0001, lr_GenModel=0.0001, u_net_gen=False, multi_gpu=True,
                 is_conditional=False,
                 is_residual=True):
        self.ImageRow = ImageRow
        self.ImageColumn = ImageColumn
        self.ImageDepth = ImageDepth
        self.D = None  # discriminator
        self.G = None  # generator
        self.DisModel = None  # discriminator model
        self.DisModel_multi_gpu = None
        self.GenModel = None  # generator model
        self.GenModel_multi_gpu = None
        self.DiscriminatorKernel = FirstDiscriminatorKernel  # profondeur des carac extraite pour le gen
        self.GeneratorKernel = FirstGeneratorKernel  # profondeur des carac extraites pour le discri
        self.lamb_adv = lamb_adv
        self.lamb_rec = lamb_rec
        self.lamb_gp = lamb_gp
        self.lr_DisModel = lr_DisModel
        self.lr_GenModel = lr_GenModel
        self.u_net_gen = u_net_gen
        self.multi_gpu = multi_gpu
        self.is_conditional = is_conditional
        self.is_residual = is_residual

    def discriminator_block(self, name):
        """Creates a discriminator model that takes an image as input and outputs a single value, representing whether
        the input is real or generated. Unlike normal GANs, the output is not sigmoid and does not represent a
        probability!
        Instead, the output should be as large and negative as possible for generated inputs and as large and positive
        as possible for real inputs.
        Note that the improved WGAN paper suggests that BatchNormalization should not be used in the discriminator."""

        # In:
        inputs = Input(shape=(2, self.ImageRow, self.ImageColumn, self.ImageDepth), name='dis_input')

        # Input 64
        disnet = Conv3D(self.DiscriminatorKernel * 1, 4, strides=2,
                        padding='same',
                        kernel_initializer='he_normal',
                        data_format='channels_first',
                        name=name + '_conv_dis_1')(inputs)
        disnet = LeakyReLU(0.01)(disnet)

        # Hidden 1 : 32
        disnet = Conv3D(self.DiscriminatorKernel * 2, 4, strides=2,
                        padding='same',
                        kernel_initializer='he_normal',
                        data_format='channels_first',
                        name=name + '_conv_dis_2')(disnet)
        disnet = LeakyReLU(0.01)(disnet)

        # Hidden 2 : 16
        disnet = Conv3D(self.DiscriminatorKernel * 4, 4, strides=2,
                        padding='same',
                        kernel_initializer='he_normal',
                        data_format='channels_first',
                        name=name + '_conv_dis_3')(disnet)
        disnet = LeakyReLU(0.01)(disnet)

        # Hidden 3 : 8
        disnet = Conv3D(self.DiscriminatorKernel * 8, 4, strides=2,
                        padding='same',
                        kernel_initializer='he_normal',
                        data_format='channels_first',
                        name=name + '_conv_dis_4')(disnet)
        disnet = LeakyReLU(0.01)(disnet)

        # Hidden 4 : 4
        disnet = Conv3D(self.DiscriminatorKernel * 16, 4, strides=2,
                        padding='same',
                        kernel_initializer='he_normal',
                        data_format='channels_first',
                        name=name + '_conv_dis_5')(disnet)
        disnet = LeakyReLU(0.01)(disnet)

        # Decision : 2
        decision = Conv3D(1, 2, strides=1,
                          use_bias=False,
                          kernel_initializer='he_normal',
                          data_format='channels_first',
                          name='dis_decision')(disnet)

        decision = Reshape((1,))(decision)

        model = Model(inputs=[inputs], outputs=[decision], name=name)
        return model

    def generator_block(self, name):  # generateur meme dim en entree et sortie si multiple de 4
        #
        inputs = Input(shape=(1, self.ImageRow, self.ImageColumn, self.ImageDepth))

        # Representation
        gennet = ReflectPadding3D(padding=3)(inputs)
        gennet = Conv3D(self.GeneratorKernel, 7, strides=1, kernel_initializer=gen_initializer,
                        use_bias=False,
                        name=name + '_gen_conv1',
                        data_format='channels_first')(gennet)
        gennet = InstanceNormalization3D(name=name + '_gen_isnorm_conv1')(gennet)
        gennet = Activation('relu')(gennet)

        # Downsampling 1
        gennet = ReflectPadding3D(padding=1)(gennet)
        gennet = Conv3D(self.GeneratorKernel * 2, 3, strides=2, kernel_initializer=gen_initializer,
                        use_bias=False,
                        name=name + '_gen_conv2',
                        data_format='channels_first')(gennet)
        gennet = InstanceNormalization3D(name=name + '_gen_isnorm_conv2')(gennet)
        gennet = Activation('relu')(gennet)

        # Downsampling 2
        gennet = ReflectPadding3D(padding=1)(gennet)
        gennet = Conv3D(self.GeneratorKernel * 4, 3, strides=2, kernel_initializer=gen_initializer,
                        use_bias=False,
                        name=name + '_gen_conv3',
                        data_format='channels_first')(gennet)
        gennet = InstanceNormalization3D(name=name + '_gen_isnorm_conv3')(gennet)
        gennet = Activation('relu')(gennet)

        # Resnet blocks : 6, 8*4 = 32
        gennet = resnet_blocks(gennet, self.GeneratorKernel * 4, name=name + '_gen_block1')
        gennet = resnet_blocks(gennet, self.GeneratorKernel * 4, name=name + '_gen_block2')
        gennet = resnet_blocks(gennet, self.GeneratorKernel * 4, name=name + '_gen_block3')
        gennet = resnet_blocks(gennet, self.GeneratorKernel * 4, name=name + '_gen_block4')
        gennet = resnet_blocks(gennet, self.GeneratorKernel * 4, name=name + '_gen_block5')
        gennet = resnet_blocks(gennet, self.GeneratorKernel * 4, name=name + '_gen_block6')

        # Upsampling 1
        gennet = UpSampling3D(size=(2, 2, 2),
                              data_format='channels_first')(gennet)
        gennet = ReflectPadding3D(padding=1)(gennet)
        gennet = Conv3D(self.GeneratorKernel * 2, 3, strides=1, kernel_initializer=gen_initializer,
                        use_bias=False,
                        name=name + '_gen_deconv1',
                        data_format='channels_first')(gennet)
        gennet = InstanceNormalization3D(name=name + '_gen_isnorm_deconv1')(gennet)
        gennet = Activation('relu')(gennet)

        # Upsampling 2
        gennet = UpSampling3D(size=(2, 2, 2),
                              data_format='channels_first')(gennet)
        gennet = ReflectPadding3D(padding=1)(gennet)
        gennet = Conv3D(self.GeneratorKernel, 3, strides=1, kernel_initializer=gen_initializer,
                        use_bias=False,
                        name=name + '_gen_deconv2',
                        data_format='channels_first')(gennet)
        gennet = InstanceNormalization3D(name=name + '_gen_isnorm_deconv2')(gennet)
        gennet = Activation('relu')(gennet)

        # Reconstruction
        gennet = ReflectPadding3D(padding=3)(gennet)
        gennet = Conv3D(2, 7, strides=1, kernel_initializer=gen_initializer,
                        use_bias=False,
                        name=name + '_gen_1conv',
                        data_format='channels_first')(gennet)

        predictions = gennet
        predictions = activation_SegSRGAN(is_residual=self.is_residual)(
            [predictions, inputs])  # sigmoid proba + add input and pred SR

        model = Model(inputs=inputs, outputs=predictions, name=name)
        return model

    def discriminator_block_conditionnal(self, name):
        """Creates a discriminator model that takes an image as input and outputs a single value, representing whether
        the input is real or generated. Unlike normal GANs, the output is not sigmoid and does not represent a
        probability!
        Instead, the output should be as large and negative as possible for generated inputs and as large and positive
        as possible for real inputs.
        Note that the improved WGAN paper suggests that BatchNormalization should not be used in the discriminator."""

        # In:
        im = Input(shape=(2, self.ImageRow, self.ImageColumn, self.ImageDepth), name='dis_input')

        res = Input(shape=(1, self.ImageRow, self.ImageColumn, self.ImageDepth), name='dis_input_res')

        inputs = Concatenate(axis=-4)([im, res])

        # Input 64
        disnet = Conv3D(self.DiscriminatorKernel * 1, 4, strides=2,
                        padding='same',
                        kernel_initializer='he_normal',
                        data_format='channels_first',
                        name=name + '_conv_dis_1')(inputs)
        disnet = LeakyReLU(0.01)(disnet)

        # Hidden 1 : 32
        disnet = Conv3D(self.DiscriminatorKernel * 2, 4, strides=2,
                        padding='same',
                        kernel_initializer='he_normal',
                        data_format='channels_first',
                        name=name + '_conv_dis_2')(disnet)
        disnet = LeakyReLU(0.01)(disnet)

        # Hidden 2 : 16
        disnet = Conv3D(self.DiscriminatorKernel * 4, 4, strides=2,
                        padding='same',
                        kernel_initializer='he_normal',
                        data_format='channels_first',
                        name=name + '_conv_dis_3')(disnet)
        disnet = LeakyReLU(0.01)(disnet)

        # Hidden 3 : 8
        disnet = Conv3D(self.DiscriminatorKernel * 8, 4, strides=2,
                        padding='same',
                        kernel_initializer='he_normal',
                        data_format='channels_first',
                        name=name + '_conv_dis_4')(disnet)
        disnet = LeakyReLU(0.01)(disnet)

        # Hidden 4 : 4
        disnet = Conv3D(self.DiscriminatorKernel * 16, 4, strides=2,
                        padding='same',
                        kernel_initializer='he_normal',
                        data_format='channels_first',
                        name=name + '_conv_dis_5')(disnet)
        disnet = LeakyReLU(0.01)(disnet)

        # Decision : 2
        decision = Conv3D(1, 2, strides=1,
                          use_bias=False,
                          kernel_initializer='he_normal',
                          data_format='channels_first',
                          name='dis_decision')(disnet)

        decision = Reshape((1,))(decision)

        model = Model(inputs=[im, res], outputs=[decision], name=name)

        return model

    def generator_block_conditionnal(self, name):  # generateur meme dim en entree et sortie si multiple de 4
        #
        im = Input(shape=(1, self.ImageRow, self.ImageColumn, self.ImageDepth), name='dis_input')

        res = Input(shape=(1, self.ImageRow, self.ImageColumn, self.ImageDepth), name='dis_input_res')

        inputs = Concatenate(axis=-4)([im, res])

        # Representation
        gennet = ReflectPadding3D(padding=3)(inputs)
        gennet = Conv3D(self.GeneratorKernel, 7, strides=1, kernel_initializer=gen_initializer,
                        use_bias=False,
                        name=name + '_gen_conv1',
                        data_format='channels_first')(gennet)
        gennet = InstanceNormalization3D(name=name + '_gen_isnorm_conv1')(gennet)
        gennet = Activation('relu')(gennet)

        # Downsampling 1
        gennet = ReflectPadding3D(padding=1)(gennet)
        gennet = Conv3D(self.GeneratorKernel * 2, 3, strides=2, kernel_initializer=gen_initializer,
                        use_bias=False,
                        name=name + '_gen_conv2',
                        data_format='channels_first')(gennet)
        gennet = InstanceNormalization3D(name=name + '_gen_isnorm_conv2')(gennet)
        gennet = Activation('relu')(gennet)

        # Downsampling 2
        gennet = ReflectPadding3D(padding=1)(gennet)
        gennet = Conv3D(self.GeneratorKernel * 4, 3, strides=2, kernel_initializer=gen_initializer,
                        use_bias=False,
                        name=name + '_gen_conv3',
                        data_format='channels_first')(gennet)
        gennet = InstanceNormalization3D(name=name + '_gen_isnorm_conv3')(gennet)
        gennet = Activation('relu')(gennet)

        # Resnet blocks : 6, 8*4 = 32
        gennet = resnet_blocks(gennet, self.GeneratorKernel * 4, name=name + '_gen_block1')
        gennet = resnet_blocks(gennet, self.GeneratorKernel * 4, name=name + '_gen_block2')
        gennet = resnet_blocks(gennet, self.GeneratorKernel * 4, name=name + '_gen_block3')
        gennet = resnet_blocks(gennet, self.GeneratorKernel * 4, name=name + '_gen_block4')
        gennet = resnet_blocks(gennet, self.GeneratorKernel * 4, name=name + '_gen_block5')
        gennet = resnet_blocks(gennet, self.GeneratorKernel * 4, name=name + '_gen_block6')

        # Upsampling 1
        gennet = UpSampling3D(size=(2, 2, 2),
                              data_format='channels_first')(gennet)
        gennet = ReflectPadding3D(padding=1)(gennet)
        gennet = Conv3D(self.GeneratorKernel * 2, 3, strides=1, kernel_initializer=gen_initializer,
                        use_bias=False,
                        name=name + '_gen_deconv1',
                        data_format='channels_first')(gennet)
        gennet = InstanceNormalization3D(name=name + '_gen_isnorm_deconv1')(gennet)
        gennet = Activation('relu')(gennet)

        # Upsampling 2
        gennet = UpSampling3D(size=(2, 2, 2),
                              data_format='channels_first')(gennet)
        gennet = ReflectPadding3D(padding=1)(gennet)
        gennet = Conv3D(self.GeneratorKernel, 3, strides=1, kernel_initializer=gen_initializer,
                        use_bias=False,
                        name=name + '_gen_deconv2',
                        data_format='channels_first')(gennet)
        gennet = InstanceNormalization3D(name=name + '_gen_isnorm_deconv2')(gennet)
        gennet = Activation('relu')(gennet)

        # Reconstruction
        gennet = ReflectPadding3D(padding=3)(gennet)
        gennet = Conv3D(2, 7, strides=1, kernel_initializer=gen_initializer,
                        use_bias=False,
                        name=name + '_gen_1conv',
                        data_format='channels_first')(gennet)

        predictions = gennet
        predictions = activation_SegSRGAN(is_residual=self.is_residual)(
            [predictions, im])  # sigmoid proba + add input and pred SR

        model = Model(inputs=[im, res], outputs=predictions, name=name)

        return model

    def generator_block_u_net(self, name):  # generateur meme dim en entree et sortie si multiple de 4
        #
        inputs = Input(shape=(1, self.ImageRow, self.ImageColumn, self.ImageDepth))

        # Representation
        gennet = ReflectPadding3D(padding=3)(inputs)
        gennet = Conv3D(self.GeneratorKernel, 7, strides=1, kernel_initializer=gen_initializer,
                        use_bias=False,
                        name=name + '_gen_conv1',
                        data_format='channels_first')(gennet)
        gennet = InstanceNormalization3D(name=name + '_gen_isnorm_conv1')(gennet)
        gennet = Activation('relu')(gennet)

        # resblock :
        gennet = resnet_blocks(gennet, self.GeneratorKernel, name=name + '_gen_block')

        # Downsampling 1
        gennet_down_1 = ReflectPadding3D(padding=1)(gennet)
        gennet_down_1 = Conv3D(self.GeneratorKernel * 2, 3, strides=2, kernel_initializer=gen_initializer,
                               use_bias=False,
                               name=name + '_gen_conv2',
                               data_format='channels_first')(gennet_down_1)
        gennet_down_1 = InstanceNormalization3D(name=name + '_gen_isnorm_conv2')(gennet_down_1)
        gennet_down_1 = Activation('relu')(gennet_down_1)

        # resblock 1 :
        gennet_down_1 = resnet_blocks(gennet_down_1, self.GeneratorKernel * 2, name=name + '_gen_block1')

        # Downsampling 2
        gennet_down_2 = ReflectPadding3D(padding=1)(gennet_down_1)
        gennet_down_2 = Conv3D(self.GeneratorKernel * 4, 3, strides=2, kernel_initializer=gen_initializer,
                               use_bias=False,
                               name=name + '_gen_conv3',
                               data_format='channels_first')(gennet_down_2)
        gennet_down_2 = InstanceNormalization3D(name=name + '_gen_isnorm_conv3')(gennet_down_2)
        gennet_down_2 = Activation('relu')(gennet_down_2)

        # resblock 2
        gennet_down_2 = resnet_blocks(gennet_down_2, self.GeneratorKernel * 4, name=name + '_gen_block2')

        # Upsampling X2 down_2 : 

        gennet_up_1 = UpSampling3D(size=(2, 2, 2),
                                   data_format='channels_first')(gennet_down_2)
        gennet_up_1 = ReflectPadding3D(padding=1)(gennet_up_1)
        gennet_up_1 = Conv3D(self.GeneratorKernel * 2, 3, strides=1, kernel_initializer=gen_initializer,
                             use_bias=False,
                             name=name + '_gen_deconv1',
                             data_format='channels_first')(gennet_up_1)
        gennet_up_1 = InstanceNormalization3D(name=name + '_gen_isnorm_deconv1')(gennet_up_1)
        gennet_up_1 = Activation('relu')(gennet_up_1)

        #        del gennet_down_2

        # Concatenante gennet_up_1 with gennet_down_1 
        gennet_concate_1 = Concatenate(axis=-4)([gennet_up_1, gennet_down_1])

        #        del gennet_up_1
        #        del gennet_down_1

        # Upsampling 2
        gennet_up_2 = UpSampling3D(size=(2, 2, 2),
                                   data_format='channels_first')(gennet_concate_1)
        gennet_up_2 = ReflectPadding3D(padding=1)(gennet_up_2)
        gennet_up_2 = Conv3D(self.GeneratorKernel, 3, strides=1, kernel_initializer=gen_initializer,
                             use_bias=False,
                             name=name + '_gen_deconv2',
                             data_format='channels_first')(gennet_up_2)
        gennet_up_2 = InstanceNormalization3D(name=name + '_gen_isnorm_deconv2')(gennet_up_2)
        gennet_up_2 = Activation('relu')(gennet_up_2)

        # Concatenante gennet_up_2 with gennet_down_1
        gennet_concate_2 = Concatenate(axis=-4)([gennet_up_2, gennet])

        #        del gennet_concate_1
        #        del gennet_up_2

        # Reconstruction
        gennet_concate_2 = ReflectPadding3D(padding=3)(gennet_concate_2)
        gennet_concate_2 = Conv3D(2, 7, strides=1, kernel_initializer=gen_initializer,
                                  use_bias=False,
                                  name=name + '_gen_1conv',
                                  data_format='channels_first')(gennet_concate_2)

        predictions = gennet_concate_2
        predictions = activation_SegSRGAN(is_residual=self.is_residual)([predictions, inputs])

        model = Model(inputs=inputs, outputs=predictions, name=name)

        return model

    def generator(self):
        if self.G:
            return self.G
        if self.u_net_gen:
            self.G = self.generator_block_u_net('G')
        elif self.is_conditional:
            self.G = self.generator_block_conditionnal('G_cond')
        else:
            self.G = self.generator_block('G')

        return self.G

    def discriminator(self):
        if self.D:
            return self.D
        if self.is_conditional:
            self.D = self.discriminator_block_conditionnal('DX_cond')
        else:
            self.D = self.discriminator_block('DX')
        return self.D

    def generator_multi_gpu(self):

        num_gpu = len(get_available_gpus())

        if self.multi_gpu and (num_gpu > 1):

            gen_multi_gpu = multi_gpu_model(self.generator(), gpus=num_gpu, cpu_merge=False)
            print("Generator duplicated on : " + str(num_gpu) + " GPUS")
        else:
            gen_multi_gpu = self.generator()

        return gen_multi_gpu

    def discri_multi_gpu(self):

        num_gpu = len(get_available_gpus())

        if self.multi_gpu and (num_gpu > 1):

            discri_multi_gpu = multi_gpu_model(self.discriminator(), gpus=num_gpu, cpu_merge=False)
            print("Generator duplicated on : " + str(num_gpu) + " GPUS")
        else:
            discri_multi_gpu = self.discriminator()

        return discri_multi_gpu

    def generator_model(self):
        if self.GenModel:
            return self.GenModel

        print("We freeze the weights of Discriminator by setting their learning rate as 0 when updating Generator !")
        # We freeze the weights of Discriminator by setting their learning rate as 0 when updating Generator !
        #        AllParameters = 63
        #        GeneratorParameters = 52
        AllParameters = len(self.generator().get_weights()) + len(self.discriminator().get_weights())
        GeneratorParameters = len(self.generator().get_weights())
        multipliers = np.ones(AllParameters)
        for idx in range(GeneratorParameters, AllParameters):
            multipliers[idx] = 0.0

        input_im = Input(shape=(1, self.ImageRow, self.ImageColumn, self.ImageDepth), name='input_im_gen')

        if self.is_conditional:

            input_res = Input(shape=(1, self.ImageRow, self.ImageColumn, self.ImageDepth), name='input_res_gen')
            Gx_gen = self.generator()([input_im, input_res])  # Fake X
            fool_decision = self.discriminator()([Gx_gen, input_res])  # Fooling D
            # Model
            self.GenModel = Model([input_im, input_res], [fool_decision, Gx_gen])

        else:

            Gx_gen = self.generator()(input_im)  # Fake X
            fool_decision = self.discriminator()(Gx_gen)  # Fooling D
            # Model
            self.GenModel = Model(input_im, [fool_decision, Gx_gen])

        # print archi :

        self.generator().summary(line_length=150)

        num_gpu = len(get_available_gpus())

        if (self.multi_gpu) & (num_gpu > 1):

            self.GenModel_multi_gpu = multi_gpu_model(self.GenModel, gpus=num_gpu, cpu_merge=False)
            print("Generator Model duplicated on : " + str(num_gpu) + " GPUS")
        else:
            self.GenModel_multi_gpu = self.GenModel
            print("Generator Model apply on CPU or single GPU")

            # self.GenModel = multi_gpu_model(self.GenModel, gpus=num_gpu)
        self.GenModel_multi_gpu.compile(LR_Adam(lr=self.lr_GenModel, beta_1=0.5, beta_2=0.999, multipliers=multipliers),
                                        loss=[wasserstein_loss, charbonnier_loss],
                                        loss_weights=[self.lamb_adv, self.lamb_rec])

        return self.GenModel, self.GenModel_multi_gpu

    def generator_model_for_pred(self):
        if self.GenModel:
            return self.GenModel

        print("We freeze the weights of Discriminator by setting their learning rate as 0 when updating Generator !")
        # We freeze the weights of Discriminator by setting their learning rate as 0 when updating Generator !
        AllParameters = 63
        GeneratorParameters = 52
        multipliers = np.ones(AllParameters)
        for idx in range(GeneratorParameters, AllParameters):
            multipliers[idx] = 0.0

        # Input
        input_gen = Input(shape=(1, self.ImageRow, self.ImageColumn, self.ImageDepth), name='input_gen')

        if self.is_conditional:

            input_res = Input(shape=(1, self.ImageRow, self.ImageColumn, self.ImageDepth), name='input_res_gen')
            Gx_gen = self.generator()([input_gen, input_res])  # Fake X
            # Model
            self.GenModel = Model([input_gen, input_res], [Gx_gen])

        else:

            Gx_gen = self.generator()(input_gen)  # Fake X
            # Model
            self.GenModel = Model(input_gen, [Gx_gen])

        # ajout d'un loss quelconque car nous en avons pas besoin pour l'application d'un model mais il ne faut pas que
        # celui-ci soit definit
        # en utilisant de discri
        self.GenModel.compile(LR_Adam(lr=self.lr_GenModel, beta_1=0.5, beta_2=0.999, multipliers=multipliers),
                              losses.mean_squared_error)

        return self.GenModel

    def discriminator_model(self):
        if self.DisModel:
            return self.DisModel

            # Input
        real_dis = Input(shape=(2, self.ImageRow, self.ImageColumn, self.ImageDepth), name='real_dis')
        fake_dis = Input(shape=(2, self.ImageRow, self.ImageColumn, self.ImageDepth), name='fake_dis')
        interp_dis = Input(shape=(2, self.ImageRow, self.ImageColumn, self.ImageDepth), name='interp_dis')

        if self.is_conditional:

            res = Input(shape=(1, self.ImageRow, self.ImageColumn, self.ImageDepth))

            # Discriminator
            real_decision = self.discriminator()([real_dis, res])  # Real X
            fake_decision = self.discriminator()([fake_dis, res])  # Fake X
            interp_decision = self.discriminator()([interp_dis, res])  # interpolation X

        else:

            # Discriminator
            real_decision = self.discriminator()(real_dis)  # Real X
            fake_decision = self.discriminator()(fake_dis)  # Fake X
            interp_decision = self.discriminator()(interp_dis)  # interpolation X

        # GP loss
        partial_gp_loss = partial(gradient_penalty_loss,
                                  averaged_samples=interp_dis,
                                  gradient_penalty_weight=self.lamb_gp)
        partial_gp_loss.__name__ = 'gradient_penalty'  # Functions need names or Keras will throw an error*      

        num_gpu = len(get_available_gpus())

        print("number of gpus : " + str(num_gpu))

        if self.is_conditional:

            # Model
            self.DisModel = Model([real_dis, fake_dis, interp_dis, res],
                                  [real_decision, fake_decision, interp_decision])
        else:
            # Model
            self.DisModel = Model([real_dis, fake_dis, interp_dis], [real_decision, fake_decision, interp_decision])

        if (self.multi_gpu) & (num_gpu > 1):

            self.DisModel_multi_gpu = multi_gpu_model(self.DisModel, gpus=num_gpu, cpu_merge=False)
            print("Discriminator Model duplicated on : " + str(num_gpu) + " GPUS")
        else:
            self.DisModel_multi_gpu = self.DisModel
            print("Discriminator Model apply on CPU or single GPU")

        # self.DisModel = multi_gpu_model(self.DisModel, gpus=num_gpu)
        self.DisModel_multi_gpu.compile(Adam(lr=self.lr_DisModel, beta_1=0.5, beta_2=0.999),
                                        loss=[wasserstein_loss, wasserstein_loss, partial_gp_loss],
                                        loss_weights=[1, 1, self.lamb_gp])
        # multi gpu training ne change rien au temps d'exectution sur romeo. meme en changeant l'argument cpu_merge=False.

        return self.DisModel, self.DisModel_multi_gpu
