import * as $protobuf from "protobufjs";
import Long = require("long");
/** Namespace bayesmech. */
export namespace bayesmech {

    /** Namespace vision. */
    namespace vision {

        /** Properties of a Pose. */
        interface IPose {

            /** Pose position */
            position?: (bayesmech.vision.IVector3|null);

            /** Pose rotation */
            rotation?: (bayesmech.vision.IQuaternion|null);
        }

        /** Represents a Pose. */
        class Pose implements IPose {

            /**
             * Constructs a new Pose.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IPose);

            /** Pose position. */
            public position?: (bayesmech.vision.IVector3|null);

            /** Pose rotation. */
            public rotation?: (bayesmech.vision.IQuaternion|null);

            /**
             * Creates a new Pose instance using the specified properties.
             * @param [properties] Properties to set
             * @returns Pose instance
             */
            public static create(properties?: bayesmech.vision.IPose): bayesmech.vision.Pose;

            /**
             * Encodes the specified Pose message. Does not implicitly {@link bayesmech.vision.Pose.verify|verify} messages.
             * @param message Pose message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IPose, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified Pose message, length delimited. Does not implicitly {@link bayesmech.vision.Pose.verify|verify} messages.
             * @param message Pose message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IPose, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes a Pose message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns Pose
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.Pose;

            /**
             * Decodes a Pose message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns Pose
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.Pose;

            /**
             * Verifies a Pose message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates a Pose message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns Pose
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.Pose;

            /**
             * Creates a plain object from a Pose message. Also converts values to other types if specified.
             * @param message Pose
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.Pose, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this Pose to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for Pose
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of a Vector3. */
        interface IVector3 {

            /** Vector3 x */
            x?: (number|null);

            /** Vector3 y */
            y?: (number|null);

            /** Vector3 z */
            z?: (number|null);
        }

        /** Represents a Vector3. */
        class Vector3 implements IVector3 {

            /**
             * Constructs a new Vector3.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IVector3);

            /** Vector3 x. */
            public x: number;

            /** Vector3 y. */
            public y: number;

            /** Vector3 z. */
            public z: number;

            /**
             * Creates a new Vector3 instance using the specified properties.
             * @param [properties] Properties to set
             * @returns Vector3 instance
             */
            public static create(properties?: bayesmech.vision.IVector3): bayesmech.vision.Vector3;

            /**
             * Encodes the specified Vector3 message. Does not implicitly {@link bayesmech.vision.Vector3.verify|verify} messages.
             * @param message Vector3 message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IVector3, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified Vector3 message, length delimited. Does not implicitly {@link bayesmech.vision.Vector3.verify|verify} messages.
             * @param message Vector3 message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IVector3, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes a Vector3 message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns Vector3
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.Vector3;

            /**
             * Decodes a Vector3 message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns Vector3
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.Vector3;

            /**
             * Verifies a Vector3 message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates a Vector3 message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns Vector3
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.Vector3;

            /**
             * Creates a plain object from a Vector3 message. Also converts values to other types if specified.
             * @param message Vector3
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.Vector3, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this Vector3 to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for Vector3
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of a Quaternion. */
        interface IQuaternion {

            /** Quaternion x */
            x?: (number|null);

            /** Quaternion y */
            y?: (number|null);

            /** Quaternion z */
            z?: (number|null);

            /** Quaternion w */
            w?: (number|null);
        }

        /** Represents a Quaternion. */
        class Quaternion implements IQuaternion {

            /**
             * Constructs a new Quaternion.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IQuaternion);

            /** Quaternion x. */
            public x: number;

            /** Quaternion y. */
            public y: number;

            /** Quaternion z. */
            public z: number;

            /** Quaternion w. */
            public w: number;

            /**
             * Creates a new Quaternion instance using the specified properties.
             * @param [properties] Properties to set
             * @returns Quaternion instance
             */
            public static create(properties?: bayesmech.vision.IQuaternion): bayesmech.vision.Quaternion;

            /**
             * Encodes the specified Quaternion message. Does not implicitly {@link bayesmech.vision.Quaternion.verify|verify} messages.
             * @param message Quaternion message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IQuaternion, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified Quaternion message, length delimited. Does not implicitly {@link bayesmech.vision.Quaternion.verify|verify} messages.
             * @param message Quaternion message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IQuaternion, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes a Quaternion message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns Quaternion
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.Quaternion;

            /**
             * Decodes a Quaternion message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns Quaternion
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.Quaternion;

            /**
             * Verifies a Quaternion message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates a Quaternion message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns Quaternion
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.Quaternion;

            /**
             * Creates a plain object from a Quaternion message. Also converts values to other types if specified.
             * @param message Quaternion
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.Quaternion, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this Quaternion to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for Quaternion
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of a PerceiverDataFrame. */
        interface IPerceiverDataFrame {

            /** PerceiverDataFrame frameIdentifier */
            frameIdentifier?: (bayesmech.vision.IPerceiverFrameIdentifier|null);

            /** PerceiverDataFrame cameraPose */
            cameraPose?: (bayesmech.vision.IPose|null);

            /** PerceiverDataFrame rgbFrame */
            rgbFrame?: (bayesmech.vision.IImageFrame|null);

            /** PerceiverDataFrame depthFrame */
            depthFrame?: (bayesmech.vision.IDepthFrame|null);

            /** PerceiverDataFrame imuData */
            imuData?: (bayesmech.vision.IImuData|null);

            /** PerceiverDataFrame cameraIntrinsics */
            cameraIntrinsics?: (bayesmech.vision.ICameraIntrinsics|null);

            /** PerceiverDataFrame inferredGeometry */
            inferredGeometry?: (bayesmech.vision.IInferredGeometry|null);

            /** PerceiverDataFrame gpsLocation */
            gpsLocation?: (bayesmech.vision.IGpsLocation|null);

            /** PerceiverDataFrame userTextInput */
            userTextInput?: (string|null);
        }

        /** Represents a PerceiverDataFrame. */
        class PerceiverDataFrame implements IPerceiverDataFrame {

            /**
             * Constructs a new PerceiverDataFrame.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IPerceiverDataFrame);

            /** PerceiverDataFrame frameIdentifier. */
            public frameIdentifier?: (bayesmech.vision.IPerceiverFrameIdentifier|null);

            /** PerceiverDataFrame cameraPose. */
            public cameraPose?: (bayesmech.vision.IPose|null);

            /** PerceiverDataFrame rgbFrame. */
            public rgbFrame?: (bayesmech.vision.IImageFrame|null);

            /** PerceiverDataFrame depthFrame. */
            public depthFrame?: (bayesmech.vision.IDepthFrame|null);

            /** PerceiverDataFrame imuData. */
            public imuData?: (bayesmech.vision.IImuData|null);

            /** PerceiverDataFrame cameraIntrinsics. */
            public cameraIntrinsics?: (bayesmech.vision.ICameraIntrinsics|null);

            /** PerceiverDataFrame inferredGeometry. */
            public inferredGeometry?: (bayesmech.vision.IInferredGeometry|null);

            /** PerceiverDataFrame gpsLocation. */
            public gpsLocation?: (bayesmech.vision.IGpsLocation|null);

            /** PerceiverDataFrame userTextInput. */
            public userTextInput: string;

            /**
             * Creates a new PerceiverDataFrame instance using the specified properties.
             * @param [properties] Properties to set
             * @returns PerceiverDataFrame instance
             */
            public static create(properties?: bayesmech.vision.IPerceiverDataFrame): bayesmech.vision.PerceiverDataFrame;

            /**
             * Encodes the specified PerceiverDataFrame message. Does not implicitly {@link bayesmech.vision.PerceiverDataFrame.verify|verify} messages.
             * @param message PerceiverDataFrame message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IPerceiverDataFrame, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified PerceiverDataFrame message, length delimited. Does not implicitly {@link bayesmech.vision.PerceiverDataFrame.verify|verify} messages.
             * @param message PerceiverDataFrame message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IPerceiverDataFrame, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes a PerceiverDataFrame message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns PerceiverDataFrame
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.PerceiverDataFrame;

            /**
             * Decodes a PerceiverDataFrame message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns PerceiverDataFrame
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.PerceiverDataFrame;

            /**
             * Verifies a PerceiverDataFrame message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates a PerceiverDataFrame message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns PerceiverDataFrame
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.PerceiverDataFrame;

            /**
             * Creates a plain object from a PerceiverDataFrame message. Also converts values to other types if specified.
             * @param message PerceiverDataFrame
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.PerceiverDataFrame, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this PerceiverDataFrame to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for PerceiverDataFrame
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of a PerceiverFrameIdentifier. */
        interface IPerceiverFrameIdentifier {

            /** PerceiverFrameIdentifier timestampNs */
            timestampNs?: (number|Long|null);

            /** PerceiverFrameIdentifier frameNumber */
            frameNumber?: (number|null);

            /** PerceiverFrameIdentifier deviceId */
            deviceId?: (string|null);
        }

        /** Represents a PerceiverFrameIdentifier. */
        class PerceiverFrameIdentifier implements IPerceiverFrameIdentifier {

            /**
             * Constructs a new PerceiverFrameIdentifier.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IPerceiverFrameIdentifier);

            /** PerceiverFrameIdentifier timestampNs. */
            public timestampNs: (number|Long);

            /** PerceiverFrameIdentifier frameNumber. */
            public frameNumber: number;

            /** PerceiverFrameIdentifier deviceId. */
            public deviceId: string;

            /**
             * Creates a new PerceiverFrameIdentifier instance using the specified properties.
             * @param [properties] Properties to set
             * @returns PerceiverFrameIdentifier instance
             */
            public static create(properties?: bayesmech.vision.IPerceiverFrameIdentifier): bayesmech.vision.PerceiverFrameIdentifier;

            /**
             * Encodes the specified PerceiverFrameIdentifier message. Does not implicitly {@link bayesmech.vision.PerceiverFrameIdentifier.verify|verify} messages.
             * @param message PerceiverFrameIdentifier message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IPerceiverFrameIdentifier, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified PerceiverFrameIdentifier message, length delimited. Does not implicitly {@link bayesmech.vision.PerceiverFrameIdentifier.verify|verify} messages.
             * @param message PerceiverFrameIdentifier message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IPerceiverFrameIdentifier, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes a PerceiverFrameIdentifier message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns PerceiverFrameIdentifier
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.PerceiverFrameIdentifier;

            /**
             * Decodes a PerceiverFrameIdentifier message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns PerceiverFrameIdentifier
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.PerceiverFrameIdentifier;

            /**
             * Verifies a PerceiverFrameIdentifier message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates a PerceiverFrameIdentifier message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns PerceiverFrameIdentifier
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.PerceiverFrameIdentifier;

            /**
             * Creates a plain object from a PerceiverFrameIdentifier message. Also converts values to other types if specified.
             * @param message PerceiverFrameIdentifier
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.PerceiverFrameIdentifier, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this PerceiverFrameIdentifier to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for PerceiverFrameIdentifier
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of a CameraIntrinsics. */
        interface ICameraIntrinsics {

            /** CameraIntrinsics fx */
            fx?: (number|null);

            /** CameraIntrinsics fy */
            fy?: (number|null);

            /** CameraIntrinsics cx */
            cx?: (number|null);

            /** CameraIntrinsics cy */
            cy?: (number|null);

            /** CameraIntrinsics imageWidth */
            imageWidth?: (number|null);

            /** CameraIntrinsics imageHeight */
            imageHeight?: (number|null);

            /** CameraIntrinsics depthWidth */
            depthWidth?: (number|null);

            /** CameraIntrinsics depthHeight */
            depthHeight?: (number|null);
        }

        /** Represents a CameraIntrinsics. */
        class CameraIntrinsics implements ICameraIntrinsics {

            /**
             * Constructs a new CameraIntrinsics.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.ICameraIntrinsics);

            /** CameraIntrinsics fx. */
            public fx: number;

            /** CameraIntrinsics fy. */
            public fy: number;

            /** CameraIntrinsics cx. */
            public cx: number;

            /** CameraIntrinsics cy. */
            public cy: number;

            /** CameraIntrinsics imageWidth. */
            public imageWidth: number;

            /** CameraIntrinsics imageHeight. */
            public imageHeight: number;

            /** CameraIntrinsics depthWidth. */
            public depthWidth: number;

            /** CameraIntrinsics depthHeight. */
            public depthHeight: number;

            /**
             * Creates a new CameraIntrinsics instance using the specified properties.
             * @param [properties] Properties to set
             * @returns CameraIntrinsics instance
             */
            public static create(properties?: bayesmech.vision.ICameraIntrinsics): bayesmech.vision.CameraIntrinsics;

            /**
             * Encodes the specified CameraIntrinsics message. Does not implicitly {@link bayesmech.vision.CameraIntrinsics.verify|verify} messages.
             * @param message CameraIntrinsics message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.ICameraIntrinsics, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified CameraIntrinsics message, length delimited. Does not implicitly {@link bayesmech.vision.CameraIntrinsics.verify|verify} messages.
             * @param message CameraIntrinsics message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.ICameraIntrinsics, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes a CameraIntrinsics message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns CameraIntrinsics
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.CameraIntrinsics;

            /**
             * Decodes a CameraIntrinsics message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns CameraIntrinsics
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.CameraIntrinsics;

            /**
             * Verifies a CameraIntrinsics message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates a CameraIntrinsics message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns CameraIntrinsics
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.CameraIntrinsics;

            /**
             * Creates a plain object from a CameraIntrinsics message. Also converts values to other types if specified.
             * @param message CameraIntrinsics
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.CameraIntrinsics, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this CameraIntrinsics to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for CameraIntrinsics
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of an ImageFrame. */
        interface IImageFrame {

            /** ImageFrame data */
            data?: (Uint8Array|null);

            /** ImageFrame format */
            format?: (bayesmech.vision.ImageFrame.ImageFormat|null);

            /** ImageFrame width */
            width?: (number|null);

            /** ImageFrame height */
            height?: (number|null);

            /** ImageFrame quality */
            quality?: (number|null);
        }

        /** Represents an ImageFrame. */
        class ImageFrame implements IImageFrame {

            /**
             * Constructs a new ImageFrame.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IImageFrame);

            /** ImageFrame data. */
            public data: Uint8Array;

            /** ImageFrame format. */
            public format: bayesmech.vision.ImageFrame.ImageFormat;

            /** ImageFrame width. */
            public width: number;

            /** ImageFrame height. */
            public height: number;

            /** ImageFrame quality. */
            public quality: number;

            /**
             * Creates a new ImageFrame instance using the specified properties.
             * @param [properties] Properties to set
             * @returns ImageFrame instance
             */
            public static create(properties?: bayesmech.vision.IImageFrame): bayesmech.vision.ImageFrame;

            /**
             * Encodes the specified ImageFrame message. Does not implicitly {@link bayesmech.vision.ImageFrame.verify|verify} messages.
             * @param message ImageFrame message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IImageFrame, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified ImageFrame message, length delimited. Does not implicitly {@link bayesmech.vision.ImageFrame.verify|verify} messages.
             * @param message ImageFrame message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IImageFrame, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes an ImageFrame message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns ImageFrame
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.ImageFrame;

            /**
             * Decodes an ImageFrame message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns ImageFrame
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.ImageFrame;

            /**
             * Verifies an ImageFrame message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates an ImageFrame message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns ImageFrame
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.ImageFrame;

            /**
             * Creates a plain object from an ImageFrame message. Also converts values to other types if specified.
             * @param message ImageFrame
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.ImageFrame, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this ImageFrame to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for ImageFrame
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        namespace ImageFrame {

            /** ImageFormat enum. */
            enum ImageFormat {
                UNKNOWN = 0,
                BITMAP_RGB = 1,
                BITMAP_RGBA = 2,
                YUV_420 = 3,
                JPEG = 4,
                GRAYSCALE = 5
            }
        }

        /** Properties of a DepthFrame. */
        interface IDepthFrame {

            /** DepthFrame data */
            data?: (Uint8Array|null);

            /** DepthFrame confidence */
            confidence?: (Uint8Array|null);

            /** DepthFrame format */
            format?: (bayesmech.vision.DepthFrame.DepthFormat|null);

            /** DepthFrame width */
            width?: (number|null);

            /** DepthFrame height */
            height?: (number|null);
        }

        /** Represents a DepthFrame. */
        class DepthFrame implements IDepthFrame {

            /**
             * Constructs a new DepthFrame.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IDepthFrame);

            /** DepthFrame data. */
            public data: Uint8Array;

            /** DepthFrame confidence. */
            public confidence: Uint8Array;

            /** DepthFrame format. */
            public format: bayesmech.vision.DepthFrame.DepthFormat;

            /** DepthFrame width. */
            public width: number;

            /** DepthFrame height. */
            public height: number;

            /**
             * Creates a new DepthFrame instance using the specified properties.
             * @param [properties] Properties to set
             * @returns DepthFrame instance
             */
            public static create(properties?: bayesmech.vision.IDepthFrame): bayesmech.vision.DepthFrame;

            /**
             * Encodes the specified DepthFrame message. Does not implicitly {@link bayesmech.vision.DepthFrame.verify|verify} messages.
             * @param message DepthFrame message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IDepthFrame, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified DepthFrame message, length delimited. Does not implicitly {@link bayesmech.vision.DepthFrame.verify|verify} messages.
             * @param message DepthFrame message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IDepthFrame, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes a DepthFrame message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns DepthFrame
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.DepthFrame;

            /**
             * Decodes a DepthFrame message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns DepthFrame
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.DepthFrame;

            /**
             * Verifies a DepthFrame message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates a DepthFrame message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns DepthFrame
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.DepthFrame;

            /**
             * Creates a plain object from a DepthFrame message. Also converts values to other types if specified.
             * @param message DepthFrame
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.DepthFrame, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this DepthFrame to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for DepthFrame
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        namespace DepthFrame {

            /** DepthFormat enum. */
            enum DepthFormat {
                DEPTH_FORMAT_UNKNOWN = 0,
                UINT16_MILLIMETERS = 1,
                FLOAT32_METERS = 2
            }
        }

        /** Properties of an ImuData. */
        interface IImuData {

            /** ImuData angularVelocity */
            angularVelocity?: (bayesmech.vision.IVector3|null);

            /** ImuData linearAcceleration */
            linearAcceleration?: (bayesmech.vision.IVector3|null);

            /** ImuData gravity */
            gravity?: (bayesmech.vision.IVector3|null);

            /** ImuData magneticField */
            magneticField?: (bayesmech.vision.IVector3|null);
        }

        /** Represents an ImuData. */
        class ImuData implements IImuData {

            /**
             * Constructs a new ImuData.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IImuData);

            /** ImuData angularVelocity. */
            public angularVelocity?: (bayesmech.vision.IVector3|null);

            /** ImuData linearAcceleration. */
            public linearAcceleration?: (bayesmech.vision.IVector3|null);

            /** ImuData gravity. */
            public gravity?: (bayesmech.vision.IVector3|null);

            /** ImuData magneticField. */
            public magneticField?: (bayesmech.vision.IVector3|null);

            /**
             * Creates a new ImuData instance using the specified properties.
             * @param [properties] Properties to set
             * @returns ImuData instance
             */
            public static create(properties?: bayesmech.vision.IImuData): bayesmech.vision.ImuData;

            /**
             * Encodes the specified ImuData message. Does not implicitly {@link bayesmech.vision.ImuData.verify|verify} messages.
             * @param message ImuData message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IImuData, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified ImuData message, length delimited. Does not implicitly {@link bayesmech.vision.ImuData.verify|verify} messages.
             * @param message ImuData message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IImuData, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes an ImuData message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns ImuData
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.ImuData;

            /**
             * Decodes an ImuData message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns ImuData
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.ImuData;

            /**
             * Verifies an ImuData message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates an ImuData message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns ImuData
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.ImuData;

            /**
             * Creates a plain object from an ImuData message. Also converts values to other types if specified.
             * @param message ImuData
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.ImuData, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this ImuData to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for ImuData
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of a GpsLocation. */
        interface IGpsLocation {

            /** GpsLocation latitude */
            latitude?: (number|null);

            /** GpsLocation longitude */
            longitude?: (number|null);

            /** GpsLocation altitude */
            altitude?: (number|null);

            /** GpsLocation accuracy */
            accuracy?: (number|null);

            /** GpsLocation bearing */
            bearing?: (number|null);

            /** GpsLocation speed */
            speed?: (number|null);

            /** GpsLocation timestampMs */
            timestampMs?: (number|Long|null);
        }

        /** Represents a GpsLocation. */
        class GpsLocation implements IGpsLocation {

            /**
             * Constructs a new GpsLocation.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IGpsLocation);

            /** GpsLocation latitude. */
            public latitude: number;

            /** GpsLocation longitude. */
            public longitude: number;

            /** GpsLocation altitude. */
            public altitude: number;

            /** GpsLocation accuracy. */
            public accuracy: number;

            /** GpsLocation bearing. */
            public bearing: number;

            /** GpsLocation speed. */
            public speed: number;

            /** GpsLocation timestampMs. */
            public timestampMs: (number|Long);

            /**
             * Creates a new GpsLocation instance using the specified properties.
             * @param [properties] Properties to set
             * @returns GpsLocation instance
             */
            public static create(properties?: bayesmech.vision.IGpsLocation): bayesmech.vision.GpsLocation;

            /**
             * Encodes the specified GpsLocation message. Does not implicitly {@link bayesmech.vision.GpsLocation.verify|verify} messages.
             * @param message GpsLocation message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IGpsLocation, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified GpsLocation message, length delimited. Does not implicitly {@link bayesmech.vision.GpsLocation.verify|verify} messages.
             * @param message GpsLocation message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IGpsLocation, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes a GpsLocation message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns GpsLocation
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.GpsLocation;

            /**
             * Decodes a GpsLocation message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns GpsLocation
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.GpsLocation;

            /**
             * Verifies a GpsLocation message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates a GpsLocation message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns GpsLocation
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.GpsLocation;

            /**
             * Creates a plain object from a GpsLocation message. Also converts values to other types if specified.
             * @param message GpsLocation
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.GpsLocation, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this GpsLocation to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for GpsLocation
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of an InferredGeometry. */
        interface IInferredGeometry {

            /** InferredGeometry planes */
            planes?: (bayesmech.vision.InferredGeometry.IPlane[]|null);

            /** InferredGeometry pointCloud */
            pointCloud?: (bayesmech.vision.InferredGeometry.ITrackedPoint[]|null);
        }

        /** Represents an InferredGeometry. */
        class InferredGeometry implements IInferredGeometry {

            /**
             * Constructs a new InferredGeometry.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IInferredGeometry);

            /** InferredGeometry planes. */
            public planes: bayesmech.vision.InferredGeometry.IPlane[];

            /** InferredGeometry pointCloud. */
            public pointCloud: bayesmech.vision.InferredGeometry.ITrackedPoint[];

            /**
             * Creates a new InferredGeometry instance using the specified properties.
             * @param [properties] Properties to set
             * @returns InferredGeometry instance
             */
            public static create(properties?: bayesmech.vision.IInferredGeometry): bayesmech.vision.InferredGeometry;

            /**
             * Encodes the specified InferredGeometry message. Does not implicitly {@link bayesmech.vision.InferredGeometry.verify|verify} messages.
             * @param message InferredGeometry message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IInferredGeometry, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified InferredGeometry message, length delimited. Does not implicitly {@link bayesmech.vision.InferredGeometry.verify|verify} messages.
             * @param message InferredGeometry message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IInferredGeometry, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes an InferredGeometry message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns InferredGeometry
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.InferredGeometry;

            /**
             * Decodes an InferredGeometry message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns InferredGeometry
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.InferredGeometry;

            /**
             * Verifies an InferredGeometry message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates an InferredGeometry message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns InferredGeometry
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.InferredGeometry;

            /**
             * Creates a plain object from an InferredGeometry message. Also converts values to other types if specified.
             * @param message InferredGeometry
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.InferredGeometry, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this InferredGeometry to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for InferredGeometry
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        namespace InferredGeometry {

            /** Properties of a Plane. */
            interface IPlane {

                /** Plane id */
                id?: (Uint8Array|null);

                /** Plane centerPose */
                centerPose?: (bayesmech.vision.IPose|null);

                /** Plane extentX */
                extentX?: (number|null);

                /** Plane extentZ */
                extentZ?: (number|null);

                /** Plane type */
                type?: (bayesmech.vision.InferredGeometry.Plane.PlaneType|null);

                /** Plane polygon */
                polygon?: (bayesmech.vision.IVector3[]|null);
            }

            /** Represents a Plane. */
            class Plane implements IPlane {

                /**
                 * Constructs a new Plane.
                 * @param [properties] Properties to set
                 */
                constructor(properties?: bayesmech.vision.InferredGeometry.IPlane);

                /** Plane id. */
                public id: Uint8Array;

                /** Plane centerPose. */
                public centerPose?: (bayesmech.vision.IPose|null);

                /** Plane extentX. */
                public extentX: number;

                /** Plane extentZ. */
                public extentZ: number;

                /** Plane type. */
                public type: bayesmech.vision.InferredGeometry.Plane.PlaneType;

                /** Plane polygon. */
                public polygon: bayesmech.vision.IVector3[];

                /**
                 * Creates a new Plane instance using the specified properties.
                 * @param [properties] Properties to set
                 * @returns Plane instance
                 */
                public static create(properties?: bayesmech.vision.InferredGeometry.IPlane): bayesmech.vision.InferredGeometry.Plane;

                /**
                 * Encodes the specified Plane message. Does not implicitly {@link bayesmech.vision.InferredGeometry.Plane.verify|verify} messages.
                 * @param message Plane message or plain object to encode
                 * @param [writer] Writer to encode to
                 * @returns Writer
                 */
                public static encode(message: bayesmech.vision.InferredGeometry.IPlane, writer?: $protobuf.Writer): $protobuf.Writer;

                /**
                 * Encodes the specified Plane message, length delimited. Does not implicitly {@link bayesmech.vision.InferredGeometry.Plane.verify|verify} messages.
                 * @param message Plane message or plain object to encode
                 * @param [writer] Writer to encode to
                 * @returns Writer
                 */
                public static encodeDelimited(message: bayesmech.vision.InferredGeometry.IPlane, writer?: $protobuf.Writer): $protobuf.Writer;

                /**
                 * Decodes a Plane message from the specified reader or buffer.
                 * @param reader Reader or buffer to decode from
                 * @param [length] Message length if known beforehand
                 * @returns Plane
                 * @throws {Error} If the payload is not a reader or valid buffer
                 * @throws {$protobuf.util.ProtocolError} If required fields are missing
                 */
                public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.InferredGeometry.Plane;

                /**
                 * Decodes a Plane message from the specified reader or buffer, length delimited.
                 * @param reader Reader or buffer to decode from
                 * @returns Plane
                 * @throws {Error} If the payload is not a reader or valid buffer
                 * @throws {$protobuf.util.ProtocolError} If required fields are missing
                 */
                public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.InferredGeometry.Plane;

                /**
                 * Verifies a Plane message.
                 * @param message Plain object to verify
                 * @returns `null` if valid, otherwise the reason why it is not
                 */
                public static verify(message: { [k: string]: any }): (string|null);

                /**
                 * Creates a Plane message from a plain object. Also converts values to their respective internal types.
                 * @param object Plain object
                 * @returns Plane
                 */
                public static fromObject(object: { [k: string]: any }): bayesmech.vision.InferredGeometry.Plane;

                /**
                 * Creates a plain object from a Plane message. Also converts values to other types if specified.
                 * @param message Plane
                 * @param [options] Conversion options
                 * @returns Plain object
                 */
                public static toObject(message: bayesmech.vision.InferredGeometry.Plane, options?: $protobuf.IConversionOptions): { [k: string]: any };

                /**
                 * Converts this Plane to JSON.
                 * @returns JSON object
                 */
                public toJSON(): { [k: string]: any };

                /**
                 * Gets the default type url for Plane
                 * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
                 * @returns The default type url
                 */
                public static getTypeUrl(typeUrlPrefix?: string): string;
            }

            namespace Plane {

                /** PlaneType enum. */
                enum PlaneType {
                    PLANE_TYPE_UNKNOWN = 0,
                    HORIZONTAL_UPWARD_FACING = 1,
                    HORIZONTAL_DOWNWARD_FACING = 2,
                    VERTICAL = 3
                }
            }

            /** Properties of a TrackedPoint. */
            interface ITrackedPoint {

                /** TrackedPoint point */
                point?: (bayesmech.vision.IVector3|null);

                /** TrackedPoint confidence */
                confidence?: (number|null);
            }

            /** Represents a TrackedPoint. */
            class TrackedPoint implements ITrackedPoint {

                /**
                 * Constructs a new TrackedPoint.
                 * @param [properties] Properties to set
                 */
                constructor(properties?: bayesmech.vision.InferredGeometry.ITrackedPoint);

                /** TrackedPoint point. */
                public point?: (bayesmech.vision.IVector3|null);

                /** TrackedPoint confidence. */
                public confidence: number;

                /**
                 * Creates a new TrackedPoint instance using the specified properties.
                 * @param [properties] Properties to set
                 * @returns TrackedPoint instance
                 */
                public static create(properties?: bayesmech.vision.InferredGeometry.ITrackedPoint): bayesmech.vision.InferredGeometry.TrackedPoint;

                /**
                 * Encodes the specified TrackedPoint message. Does not implicitly {@link bayesmech.vision.InferredGeometry.TrackedPoint.verify|verify} messages.
                 * @param message TrackedPoint message or plain object to encode
                 * @param [writer] Writer to encode to
                 * @returns Writer
                 */
                public static encode(message: bayesmech.vision.InferredGeometry.ITrackedPoint, writer?: $protobuf.Writer): $protobuf.Writer;

                /**
                 * Encodes the specified TrackedPoint message, length delimited. Does not implicitly {@link bayesmech.vision.InferredGeometry.TrackedPoint.verify|verify} messages.
                 * @param message TrackedPoint message or plain object to encode
                 * @param [writer] Writer to encode to
                 * @returns Writer
                 */
                public static encodeDelimited(message: bayesmech.vision.InferredGeometry.ITrackedPoint, writer?: $protobuf.Writer): $protobuf.Writer;

                /**
                 * Decodes a TrackedPoint message from the specified reader or buffer.
                 * @param reader Reader or buffer to decode from
                 * @param [length] Message length if known beforehand
                 * @returns TrackedPoint
                 * @throws {Error} If the payload is not a reader or valid buffer
                 * @throws {$protobuf.util.ProtocolError} If required fields are missing
                 */
                public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.InferredGeometry.TrackedPoint;

                /**
                 * Decodes a TrackedPoint message from the specified reader or buffer, length delimited.
                 * @param reader Reader or buffer to decode from
                 * @returns TrackedPoint
                 * @throws {Error} If the payload is not a reader or valid buffer
                 * @throws {$protobuf.util.ProtocolError} If required fields are missing
                 */
                public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.InferredGeometry.TrackedPoint;

                /**
                 * Verifies a TrackedPoint message.
                 * @param message Plain object to verify
                 * @returns `null` if valid, otherwise the reason why it is not
                 */
                public static verify(message: { [k: string]: any }): (string|null);

                /**
                 * Creates a TrackedPoint message from a plain object. Also converts values to their respective internal types.
                 * @param object Plain object
                 * @returns TrackedPoint
                 */
                public static fromObject(object: { [k: string]: any }): bayesmech.vision.InferredGeometry.TrackedPoint;

                /**
                 * Creates a plain object from a TrackedPoint message. Also converts values to other types if specified.
                 * @param message TrackedPoint
                 * @param [options] Conversion options
                 * @returns Plain object
                 */
                public static toObject(message: bayesmech.vision.InferredGeometry.TrackedPoint, options?: $protobuf.IConversionOptions): { [k: string]: any };

                /**
                 * Converts this TrackedPoint to JSON.
                 * @returns JSON object
                 */
                public toJSON(): { [k: string]: any };

                /**
                 * Gets the default type url for TrackedPoint
                 * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
                 * @returns The default type url
                 */
                public static getTypeUrl(typeUrlPrefix?: string): string;
            }
        }

        /** Properties of a SegmentationResponse. */
        interface ISegmentationResponse {

            /** SegmentationResponse frameIdentifier */
            frameIdentifier?: (bayesmech.vision.IPerceiverFrameIdentifier|null);

            /** SegmentationResponse masks */
            masks?: (bayesmech.vision.SegmentationResponse.ISegmentationMask[]|null);

            /** SegmentationResponse triggerType */
            triggerType?: (bayesmech.vision.SegmentationResponse.SegmentationTriggerType|null);
        }

        /** Represents a SegmentationResponse. */
        class SegmentationResponse implements ISegmentationResponse {

            /**
             * Constructs a new SegmentationResponse.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.ISegmentationResponse);

            /** SegmentationResponse frameIdentifier. */
            public frameIdentifier?: (bayesmech.vision.IPerceiverFrameIdentifier|null);

            /** SegmentationResponse masks. */
            public masks: bayesmech.vision.SegmentationResponse.ISegmentationMask[];

            /** SegmentationResponse triggerType. */
            public triggerType: bayesmech.vision.SegmentationResponse.SegmentationTriggerType;

            /**
             * Creates a new SegmentationResponse instance using the specified properties.
             * @param [properties] Properties to set
             * @returns SegmentationResponse instance
             */
            public static create(properties?: bayesmech.vision.ISegmentationResponse): bayesmech.vision.SegmentationResponse;

            /**
             * Encodes the specified SegmentationResponse message. Does not implicitly {@link bayesmech.vision.SegmentationResponse.verify|verify} messages.
             * @param message SegmentationResponse message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.ISegmentationResponse, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified SegmentationResponse message, length delimited. Does not implicitly {@link bayesmech.vision.SegmentationResponse.verify|verify} messages.
             * @param message SegmentationResponse message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.ISegmentationResponse, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes a SegmentationResponse message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns SegmentationResponse
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.SegmentationResponse;

            /**
             * Decodes a SegmentationResponse message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns SegmentationResponse
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.SegmentationResponse;

            /**
             * Verifies a SegmentationResponse message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates a SegmentationResponse message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns SegmentationResponse
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.SegmentationResponse;

            /**
             * Creates a plain object from a SegmentationResponse message. Also converts values to other types if specified.
             * @param message SegmentationResponse
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.SegmentationResponse, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this SegmentationResponse to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for SegmentationResponse
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        namespace SegmentationResponse {

            /** Properties of a SegmentationMask. */
            interface ISegmentationMask {

                /** SegmentationMask objectId */
                objectId?: (number|null);

                /** SegmentationMask maskData */
                maskData?: (Uint8Array|null);

                /** SegmentationMask confidence */
                confidence?: (number|null);

                /** SegmentationMask pixelCount */
                pixelCount?: (number|null);

                /** SegmentationMask label */
                label?: (string|null);
            }

            /** Represents a SegmentationMask. */
            class SegmentationMask implements ISegmentationMask {

                /**
                 * Constructs a new SegmentationMask.
                 * @param [properties] Properties to set
                 */
                constructor(properties?: bayesmech.vision.SegmentationResponse.ISegmentationMask);

                /** SegmentationMask objectId. */
                public objectId: number;

                /** SegmentationMask maskData. */
                public maskData: Uint8Array;

                /** SegmentationMask confidence. */
                public confidence: number;

                /** SegmentationMask pixelCount. */
                public pixelCount: number;

                /** SegmentationMask label. */
                public label: string;

                /**
                 * Creates a new SegmentationMask instance using the specified properties.
                 * @param [properties] Properties to set
                 * @returns SegmentationMask instance
                 */
                public static create(properties?: bayesmech.vision.SegmentationResponse.ISegmentationMask): bayesmech.vision.SegmentationResponse.SegmentationMask;

                /**
                 * Encodes the specified SegmentationMask message. Does not implicitly {@link bayesmech.vision.SegmentationResponse.SegmentationMask.verify|verify} messages.
                 * @param message SegmentationMask message or plain object to encode
                 * @param [writer] Writer to encode to
                 * @returns Writer
                 */
                public static encode(message: bayesmech.vision.SegmentationResponse.ISegmentationMask, writer?: $protobuf.Writer): $protobuf.Writer;

                /**
                 * Encodes the specified SegmentationMask message, length delimited. Does not implicitly {@link bayesmech.vision.SegmentationResponse.SegmentationMask.verify|verify} messages.
                 * @param message SegmentationMask message or plain object to encode
                 * @param [writer] Writer to encode to
                 * @returns Writer
                 */
                public static encodeDelimited(message: bayesmech.vision.SegmentationResponse.ISegmentationMask, writer?: $protobuf.Writer): $protobuf.Writer;

                /**
                 * Decodes a SegmentationMask message from the specified reader or buffer.
                 * @param reader Reader or buffer to decode from
                 * @param [length] Message length if known beforehand
                 * @returns SegmentationMask
                 * @throws {Error} If the payload is not a reader or valid buffer
                 * @throws {$protobuf.util.ProtocolError} If required fields are missing
                 */
                public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.SegmentationResponse.SegmentationMask;

                /**
                 * Decodes a SegmentationMask message from the specified reader or buffer, length delimited.
                 * @param reader Reader or buffer to decode from
                 * @returns SegmentationMask
                 * @throws {Error} If the payload is not a reader or valid buffer
                 * @throws {$protobuf.util.ProtocolError} If required fields are missing
                 */
                public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.SegmentationResponse.SegmentationMask;

                /**
                 * Verifies a SegmentationMask message.
                 * @param message Plain object to verify
                 * @returns `null` if valid, otherwise the reason why it is not
                 */
                public static verify(message: { [k: string]: any }): (string|null);

                /**
                 * Creates a SegmentationMask message from a plain object. Also converts values to their respective internal types.
                 * @param object Plain object
                 * @returns SegmentationMask
                 */
                public static fromObject(object: { [k: string]: any }): bayesmech.vision.SegmentationResponse.SegmentationMask;

                /**
                 * Creates a plain object from a SegmentationMask message. Also converts values to other types if specified.
                 * @param message SegmentationMask
                 * @param [options] Conversion options
                 * @returns Plain object
                 */
                public static toObject(message: bayesmech.vision.SegmentationResponse.SegmentationMask, options?: $protobuf.IConversionOptions): { [k: string]: any };

                /**
                 * Converts this SegmentationMask to JSON.
                 * @returns JSON object
                 */
                public toJSON(): { [k: string]: any };

                /**
                 * Gets the default type url for SegmentationMask
                 * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
                 * @returns The default type url
                 */
                public static getTypeUrl(typeUrlPrefix?: string): string;
            }

            /** SegmentationTriggerType enum. */
            enum SegmentationTriggerType {
                UNKNOWN = 0,
                POINT = 1,
                TEXT = 2,
                AUTO_GRID = 3,
                PROPAGATION = 4
            }
        }

        /** Properties of a SegmentationRequest. */
        interface ISegmentationRequest {

            /** SegmentationRequest frameIdentifier */
            frameIdentifier?: (bayesmech.vision.IPerceiverFrameIdentifier|null);

            /** SegmentationRequest imageFrame */
            imageFrame?: (bayesmech.vision.IImageFrame|null);
        }

        /** Represents a SegmentationRequest. */
        class SegmentationRequest implements ISegmentationRequest {

            /**
             * Constructs a new SegmentationRequest.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.ISegmentationRequest);

            /** SegmentationRequest frameIdentifier. */
            public frameIdentifier?: (bayesmech.vision.IPerceiverFrameIdentifier|null);

            /** SegmentationRequest imageFrame. */
            public imageFrame?: (bayesmech.vision.IImageFrame|null);

            /**
             * Creates a new SegmentationRequest instance using the specified properties.
             * @param [properties] Properties to set
             * @returns SegmentationRequest instance
             */
            public static create(properties?: bayesmech.vision.ISegmentationRequest): bayesmech.vision.SegmentationRequest;

            /**
             * Encodes the specified SegmentationRequest message. Does not implicitly {@link bayesmech.vision.SegmentationRequest.verify|verify} messages.
             * @param message SegmentationRequest message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.ISegmentationRequest, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified SegmentationRequest message, length delimited. Does not implicitly {@link bayesmech.vision.SegmentationRequest.verify|verify} messages.
             * @param message SegmentationRequest message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.ISegmentationRequest, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes a SegmentationRequest message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns SegmentationRequest
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.SegmentationRequest;

            /**
             * Decodes a SegmentationRequest message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns SegmentationRequest
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.SegmentationRequest;

            /**
             * Verifies a SegmentationRequest message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates a SegmentationRequest message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns SegmentationRequest
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.SegmentationRequest;

            /**
             * Creates a plain object from a SegmentationRequest message. Also converts values to other types if specified.
             * @param message SegmentationRequest
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.SegmentationRequest, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this SegmentationRequest to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for SegmentationRequest
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of a MotionCaptureRequest. */
        interface IMotionCaptureRequest {

            /** MotionCaptureRequest frameIdentifier */
            frameIdentifier?: (bayesmech.vision.IPerceiverFrameIdentifier|null);

            /** MotionCaptureRequest currentFrame */
            currentFrame?: (bayesmech.vision.IImageFrame|null);

            /** MotionCaptureRequest referenceFrame */
            referenceFrame?: (bayesmech.vision.IImageFrame|null);
        }

        /** Represents a MotionCaptureRequest. */
        class MotionCaptureRequest implements IMotionCaptureRequest {

            /**
             * Constructs a new MotionCaptureRequest.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IMotionCaptureRequest);

            /** MotionCaptureRequest frameIdentifier. */
            public frameIdentifier?: (bayesmech.vision.IPerceiverFrameIdentifier|null);

            /** MotionCaptureRequest currentFrame. */
            public currentFrame?: (bayesmech.vision.IImageFrame|null);

            /** MotionCaptureRequest referenceFrame. */
            public referenceFrame?: (bayesmech.vision.IImageFrame|null);

            /**
             * Creates a new MotionCaptureRequest instance using the specified properties.
             * @param [properties] Properties to set
             * @returns MotionCaptureRequest instance
             */
            public static create(properties?: bayesmech.vision.IMotionCaptureRequest): bayesmech.vision.MotionCaptureRequest;

            /**
             * Encodes the specified MotionCaptureRequest message. Does not implicitly {@link bayesmech.vision.MotionCaptureRequest.verify|verify} messages.
             * @param message MotionCaptureRequest message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IMotionCaptureRequest, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified MotionCaptureRequest message, length delimited. Does not implicitly {@link bayesmech.vision.MotionCaptureRequest.verify|verify} messages.
             * @param message MotionCaptureRequest message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IMotionCaptureRequest, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes a MotionCaptureRequest message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns MotionCaptureRequest
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.MotionCaptureRequest;

            /**
             * Decodes a MotionCaptureRequest message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns MotionCaptureRequest
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.MotionCaptureRequest;

            /**
             * Verifies a MotionCaptureRequest message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates a MotionCaptureRequest message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns MotionCaptureRequest
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.MotionCaptureRequest;

            /**
             * Creates a plain object from a MotionCaptureRequest message. Also converts values to other types if specified.
             * @param message MotionCaptureRequest
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.MotionCaptureRequest, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this MotionCaptureRequest to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for MotionCaptureRequest
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of a MotionCaptureResponse. */
        interface IMotionCaptureResponse {

            /** MotionCaptureResponse frameIdentifier */
            frameIdentifier?: (bayesmech.vision.IPerceiverFrameIdentifier|null);

            /** MotionCaptureResponse heatmap */
            heatmap?: (bayesmech.vision.MotionCaptureResponse.IMotionHeatmap|null);

            /** MotionCaptureResponse methodUsed */
            methodUsed?: (bayesmech.vision.MotionCaptureResponse.StabilizationMethod|null);

            /** MotionCaptureResponse stabilizationConfidence */
            stabilizationConfidence?: (number|null);

            /** MotionCaptureResponse tracks */
            tracks?: (bayesmech.vision.IMotionTrack[]|null);

            /** MotionCaptureResponse totalFrames */
            totalFrames?: (number|null);
        }

        /** Represents a MotionCaptureResponse. */
        class MotionCaptureResponse implements IMotionCaptureResponse {

            /**
             * Constructs a new MotionCaptureResponse.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IMotionCaptureResponse);

            /** MotionCaptureResponse frameIdentifier. */
            public frameIdentifier?: (bayesmech.vision.IPerceiverFrameIdentifier|null);

            /** MotionCaptureResponse heatmap. */
            public heatmap?: (bayesmech.vision.MotionCaptureResponse.IMotionHeatmap|null);

            /** MotionCaptureResponse methodUsed. */
            public methodUsed: bayesmech.vision.MotionCaptureResponse.StabilizationMethod;

            /** MotionCaptureResponse stabilizationConfidence. */
            public stabilizationConfidence: number;

            /** MotionCaptureResponse tracks. */
            public tracks: bayesmech.vision.IMotionTrack[];

            /** MotionCaptureResponse totalFrames. */
            public totalFrames: number;

            /**
             * Creates a new MotionCaptureResponse instance using the specified properties.
             * @param [properties] Properties to set
             * @returns MotionCaptureResponse instance
             */
            public static create(properties?: bayesmech.vision.IMotionCaptureResponse): bayesmech.vision.MotionCaptureResponse;

            /**
             * Encodes the specified MotionCaptureResponse message. Does not implicitly {@link bayesmech.vision.MotionCaptureResponse.verify|verify} messages.
             * @param message MotionCaptureResponse message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IMotionCaptureResponse, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified MotionCaptureResponse message, length delimited. Does not implicitly {@link bayesmech.vision.MotionCaptureResponse.verify|verify} messages.
             * @param message MotionCaptureResponse message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IMotionCaptureResponse, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes a MotionCaptureResponse message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns MotionCaptureResponse
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.MotionCaptureResponse;

            /**
             * Decodes a MotionCaptureResponse message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns MotionCaptureResponse
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.MotionCaptureResponse;

            /**
             * Verifies a MotionCaptureResponse message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates a MotionCaptureResponse message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns MotionCaptureResponse
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.MotionCaptureResponse;

            /**
             * Creates a plain object from a MotionCaptureResponse message. Also converts values to other types if specified.
             * @param message MotionCaptureResponse
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.MotionCaptureResponse, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this MotionCaptureResponse to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for MotionCaptureResponse
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        namespace MotionCaptureResponse {

            /** Properties of a MotionHeatmap. */
            interface IMotionHeatmap {

                /** MotionHeatmap heatmapData */
                heatmapData?: (Uint8Array|null);

                /** MotionHeatmap maxMotionRaw */
                maxMotionRaw?: (number|null);
            }

            /** Represents a MotionHeatmap. */
            class MotionHeatmap implements IMotionHeatmap {

                /**
                 * Constructs a new MotionHeatmap.
                 * @param [properties] Properties to set
                 */
                constructor(properties?: bayesmech.vision.MotionCaptureResponse.IMotionHeatmap);

                /** MotionHeatmap heatmapData. */
                public heatmapData: Uint8Array;

                /** MotionHeatmap maxMotionRaw. */
                public maxMotionRaw: number;

                /**
                 * Creates a new MotionHeatmap instance using the specified properties.
                 * @param [properties] Properties to set
                 * @returns MotionHeatmap instance
                 */
                public static create(properties?: bayesmech.vision.MotionCaptureResponse.IMotionHeatmap): bayesmech.vision.MotionCaptureResponse.MotionHeatmap;

                /**
                 * Encodes the specified MotionHeatmap message. Does not implicitly {@link bayesmech.vision.MotionCaptureResponse.MotionHeatmap.verify|verify} messages.
                 * @param message MotionHeatmap message or plain object to encode
                 * @param [writer] Writer to encode to
                 * @returns Writer
                 */
                public static encode(message: bayesmech.vision.MotionCaptureResponse.IMotionHeatmap, writer?: $protobuf.Writer): $protobuf.Writer;

                /**
                 * Encodes the specified MotionHeatmap message, length delimited. Does not implicitly {@link bayesmech.vision.MotionCaptureResponse.MotionHeatmap.verify|verify} messages.
                 * @param message MotionHeatmap message or plain object to encode
                 * @param [writer] Writer to encode to
                 * @returns Writer
                 */
                public static encodeDelimited(message: bayesmech.vision.MotionCaptureResponse.IMotionHeatmap, writer?: $protobuf.Writer): $protobuf.Writer;

                /**
                 * Decodes a MotionHeatmap message from the specified reader or buffer.
                 * @param reader Reader or buffer to decode from
                 * @param [length] Message length if known beforehand
                 * @returns MotionHeatmap
                 * @throws {Error} If the payload is not a reader or valid buffer
                 * @throws {$protobuf.util.ProtocolError} If required fields are missing
                 */
                public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.MotionCaptureResponse.MotionHeatmap;

                /**
                 * Decodes a MotionHeatmap message from the specified reader or buffer, length delimited.
                 * @param reader Reader or buffer to decode from
                 * @returns MotionHeatmap
                 * @throws {Error} If the payload is not a reader or valid buffer
                 * @throws {$protobuf.util.ProtocolError} If required fields are missing
                 */
                public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.MotionCaptureResponse.MotionHeatmap;

                /**
                 * Verifies a MotionHeatmap message.
                 * @param message Plain object to verify
                 * @returns `null` if valid, otherwise the reason why it is not
                 */
                public static verify(message: { [k: string]: any }): (string|null);

                /**
                 * Creates a MotionHeatmap message from a plain object. Also converts values to their respective internal types.
                 * @param object Plain object
                 * @returns MotionHeatmap
                 */
                public static fromObject(object: { [k: string]: any }): bayesmech.vision.MotionCaptureResponse.MotionHeatmap;

                /**
                 * Creates a plain object from a MotionHeatmap message. Also converts values to other types if specified.
                 * @param message MotionHeatmap
                 * @param [options] Conversion options
                 * @returns Plain object
                 */
                public static toObject(message: bayesmech.vision.MotionCaptureResponse.MotionHeatmap, options?: $protobuf.IConversionOptions): { [k: string]: any };

                /**
                 * Converts this MotionHeatmap to JSON.
                 * @returns JSON object
                 */
                public toJSON(): { [k: string]: any };

                /**
                 * Gets the default type url for MotionHeatmap
                 * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
                 * @returns The default type url
                 */
                public static getTypeUrl(typeUrlPrefix?: string): string;
            }

            /** StabilizationMethod enum. */
            enum StabilizationMethod {
                STABILIZATION_UNKNOWN = 0,
                DEPTH_WARP = 1,
                PLANE_HOMO = 2,
                POINT_RANSAC = 3,
                POSE_APPROX = 4,
                OPTICAL_FLOW = 5
            }
        }

        /** Properties of a MotionTrackPoint. */
        interface IMotionTrackPoint {

            /** MotionTrackPoint frameIdx */
            frameIdx?: (number|null);

            /** MotionTrackPoint cx */
            cx?: (number|null);

            /** MotionTrackPoint cy */
            cy?: (number|null);

            /** MotionTrackPoint area */
            area?: (number|null);

            /** MotionTrackPoint interpolated */
            interpolated?: (boolean|null);
        }

        /** Represents a MotionTrackPoint. */
        class MotionTrackPoint implements IMotionTrackPoint {

            /**
             * Constructs a new MotionTrackPoint.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IMotionTrackPoint);

            /** MotionTrackPoint frameIdx. */
            public frameIdx: number;

            /** MotionTrackPoint cx. */
            public cx: number;

            /** MotionTrackPoint cy. */
            public cy: number;

            /** MotionTrackPoint area. */
            public area: number;

            /** MotionTrackPoint interpolated. */
            public interpolated: boolean;

            /**
             * Creates a new MotionTrackPoint instance using the specified properties.
             * @param [properties] Properties to set
             * @returns MotionTrackPoint instance
             */
            public static create(properties?: bayesmech.vision.IMotionTrackPoint): bayesmech.vision.MotionTrackPoint;

            /**
             * Encodes the specified MotionTrackPoint message. Does not implicitly {@link bayesmech.vision.MotionTrackPoint.verify|verify} messages.
             * @param message MotionTrackPoint message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IMotionTrackPoint, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified MotionTrackPoint message, length delimited. Does not implicitly {@link bayesmech.vision.MotionTrackPoint.verify|verify} messages.
             * @param message MotionTrackPoint message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IMotionTrackPoint, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes a MotionTrackPoint message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns MotionTrackPoint
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.MotionTrackPoint;

            /**
             * Decodes a MotionTrackPoint message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns MotionTrackPoint
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.MotionTrackPoint;

            /**
             * Verifies a MotionTrackPoint message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates a MotionTrackPoint message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns MotionTrackPoint
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.MotionTrackPoint;

            /**
             * Creates a plain object from a MotionTrackPoint message. Also converts values to other types if specified.
             * @param message MotionTrackPoint
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.MotionTrackPoint, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this MotionTrackPoint to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for MotionTrackPoint
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of a MotionTrack. */
        interface IMotionTrack {

            /** MotionTrack trackId */
            trackId?: (number|null);

            /** MotionTrack detectedFrames */
            detectedFrames?: (number|null);

            /** MotionTrack totalPositions */
            totalPositions?: (number|null);

            /** MotionTrack presenceFraction */
            presenceFraction?: (number|null);

            /** MotionTrack positions */
            positions?: (bayesmech.vision.IMotionTrackPoint[]|null);
        }

        /** Represents a MotionTrack. */
        class MotionTrack implements IMotionTrack {

            /**
             * Constructs a new MotionTrack.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IMotionTrack);

            /** MotionTrack trackId. */
            public trackId: number;

            /** MotionTrack detectedFrames. */
            public detectedFrames: number;

            /** MotionTrack totalPositions. */
            public totalPositions: number;

            /** MotionTrack presenceFraction. */
            public presenceFraction: number;

            /** MotionTrack positions. */
            public positions: bayesmech.vision.IMotionTrackPoint[];

            /**
             * Creates a new MotionTrack instance using the specified properties.
             * @param [properties] Properties to set
             * @returns MotionTrack instance
             */
            public static create(properties?: bayesmech.vision.IMotionTrack): bayesmech.vision.MotionTrack;

            /**
             * Encodes the specified MotionTrack message. Does not implicitly {@link bayesmech.vision.MotionTrack.verify|verify} messages.
             * @param message MotionTrack message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IMotionTrack, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified MotionTrack message, length delimited. Does not implicitly {@link bayesmech.vision.MotionTrack.verify|verify} messages.
             * @param message MotionTrack message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IMotionTrack, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes a MotionTrack message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns MotionTrack
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.MotionTrack;

            /**
             * Decodes a MotionTrack message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns MotionTrack
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.MotionTrack;

            /**
             * Verifies a MotionTrack message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates a MotionTrack message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns MotionTrack
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.MotionTrack;

            /**
             * Creates a plain object from a MotionTrack message. Also converts values to other types if specified.
             * @param message MotionTrack
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.MotionTrack, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this MotionTrack to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for MotionTrack
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of an IdoSlamFramePose. */
        interface IIdoSlamFramePose {

            /** IdoSlamFramePose frameId */
            frameId?: (bayesmech.vision.IPerceiverFrameIdentifier|null);

            /** IdoSlamFramePose frameIndex */
            frameIndex?: (number|null);

            /** IdoSlamFramePose worldPose */
            worldPose?: (bayesmech.vision.IPose|null);

            /** IdoSlamFramePose eulerDegrees */
            eulerDegrees?: (bayesmech.vision.IVector3|null);
        }

        /** Represents an IdoSlamFramePose. */
        class IdoSlamFramePose implements IIdoSlamFramePose {

            /**
             * Constructs a new IdoSlamFramePose.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IIdoSlamFramePose);

            /** IdoSlamFramePose frameId. */
            public frameId?: (bayesmech.vision.IPerceiverFrameIdentifier|null);

            /** IdoSlamFramePose frameIndex. */
            public frameIndex: number;

            /** IdoSlamFramePose worldPose. */
            public worldPose?: (bayesmech.vision.IPose|null);

            /** IdoSlamFramePose eulerDegrees. */
            public eulerDegrees?: (bayesmech.vision.IVector3|null);

            /**
             * Creates a new IdoSlamFramePose instance using the specified properties.
             * @param [properties] Properties to set
             * @returns IdoSlamFramePose instance
             */
            public static create(properties?: bayesmech.vision.IIdoSlamFramePose): bayesmech.vision.IdoSlamFramePose;

            /**
             * Encodes the specified IdoSlamFramePose message. Does not implicitly {@link bayesmech.vision.IdoSlamFramePose.verify|verify} messages.
             * @param message IdoSlamFramePose message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IIdoSlamFramePose, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified IdoSlamFramePose message, length delimited. Does not implicitly {@link bayesmech.vision.IdoSlamFramePose.verify|verify} messages.
             * @param message IdoSlamFramePose message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IIdoSlamFramePose, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes an IdoSlamFramePose message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns IdoSlamFramePose
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.IdoSlamFramePose;

            /**
             * Decodes an IdoSlamFramePose message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns IdoSlamFramePose
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.IdoSlamFramePose;

            /**
             * Verifies an IdoSlamFramePose message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates an IdoSlamFramePose message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns IdoSlamFramePose
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.IdoSlamFramePose;

            /**
             * Creates a plain object from an IdoSlamFramePose message. Also converts values to other types if specified.
             * @param message IdoSlamFramePose
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.IdoSlamFramePose, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this IdoSlamFramePose to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for IdoSlamFramePose
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of an IdoSlamGroundPoint. */
        interface IIdoSlamGroundPoint {

            /** IdoSlamGroundPoint frameIndex */
            frameIndex?: (number|null);

            /** IdoSlamGroundPoint pairedFrameIndex */
            pairedFrameIndex?: (number|null);

            /** IdoSlamGroundPoint point */
            point?: (bayesmech.vision.IVector3|null);

            /** IdoSlamGroundPoint side */
            side?: (string|null);
        }

        /** Represents an IdoSlamGroundPoint. */
        class IdoSlamGroundPoint implements IIdoSlamGroundPoint {

            /**
             * Constructs a new IdoSlamGroundPoint.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IIdoSlamGroundPoint);

            /** IdoSlamGroundPoint frameIndex. */
            public frameIndex: number;

            /** IdoSlamGroundPoint pairedFrameIndex. */
            public pairedFrameIndex: number;

            /** IdoSlamGroundPoint point. */
            public point?: (bayesmech.vision.IVector3|null);

            /** IdoSlamGroundPoint side. */
            public side: string;

            /**
             * Creates a new IdoSlamGroundPoint instance using the specified properties.
             * @param [properties] Properties to set
             * @returns IdoSlamGroundPoint instance
             */
            public static create(properties?: bayesmech.vision.IIdoSlamGroundPoint): bayesmech.vision.IdoSlamGroundPoint;

            /**
             * Encodes the specified IdoSlamGroundPoint message. Does not implicitly {@link bayesmech.vision.IdoSlamGroundPoint.verify|verify} messages.
             * @param message IdoSlamGroundPoint message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IIdoSlamGroundPoint, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified IdoSlamGroundPoint message, length delimited. Does not implicitly {@link bayesmech.vision.IdoSlamGroundPoint.verify|verify} messages.
             * @param message IdoSlamGroundPoint message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IIdoSlamGroundPoint, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes an IdoSlamGroundPoint message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns IdoSlamGroundPoint
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.IdoSlamGroundPoint;

            /**
             * Decodes an IdoSlamGroundPoint message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns IdoSlamGroundPoint
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.IdoSlamGroundPoint;

            /**
             * Verifies an IdoSlamGroundPoint message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates an IdoSlamGroundPoint message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns IdoSlamGroundPoint
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.IdoSlamGroundPoint;

            /**
             * Creates a plain object from an IdoSlamGroundPoint message. Also converts values to other types if specified.
             * @param message IdoSlamGroundPoint
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.IdoSlamGroundPoint, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this IdoSlamGroundPoint to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for IdoSlamGroundPoint
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of an IdoSlamPointCorrespondence. */
        interface IIdoSlamPointCorrespondence {

            /** IdoSlamPointCorrespondence sourceX */
            sourceX?: (number|null);

            /** IdoSlamPointCorrespondence sourceY */
            sourceY?: (number|null);

            /** IdoSlamPointCorrespondence targetX */
            targetX?: (number|null);

            /** IdoSlamPointCorrespondence targetY */
            targetY?: (number|null);

            /** IdoSlamPointCorrespondence worldPoint */
            worldPoint?: (bayesmech.vision.IVector3|null);

            /** IdoSlamPointCorrespondence side */
            side?: (string|null);

            /** IdoSlamPointCorrespondence onRoad */
            onRoad?: (boolean|null);

            /** IdoSlamPointCorrespondence triangulated */
            triangulated?: (boolean|null);

            /** IdoSlamPointCorrespondence inlier */
            inlier?: (boolean|null);
        }

        /** Represents an IdoSlamPointCorrespondence. */
        class IdoSlamPointCorrespondence implements IIdoSlamPointCorrespondence {

            /**
             * Constructs a new IdoSlamPointCorrespondence.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IIdoSlamPointCorrespondence);

            /** IdoSlamPointCorrespondence sourceX. */
            public sourceX: number;

            /** IdoSlamPointCorrespondence sourceY. */
            public sourceY: number;

            /** IdoSlamPointCorrespondence targetX. */
            public targetX: number;

            /** IdoSlamPointCorrespondence targetY. */
            public targetY: number;

            /** IdoSlamPointCorrespondence worldPoint. */
            public worldPoint?: (bayesmech.vision.IVector3|null);

            /** IdoSlamPointCorrespondence side. */
            public side: string;

            /** IdoSlamPointCorrespondence onRoad. */
            public onRoad: boolean;

            /** IdoSlamPointCorrespondence triangulated. */
            public triangulated: boolean;

            /** IdoSlamPointCorrespondence inlier. */
            public inlier: boolean;

            /**
             * Creates a new IdoSlamPointCorrespondence instance using the specified properties.
             * @param [properties] Properties to set
             * @returns IdoSlamPointCorrespondence instance
             */
            public static create(properties?: bayesmech.vision.IIdoSlamPointCorrespondence): bayesmech.vision.IdoSlamPointCorrespondence;

            /**
             * Encodes the specified IdoSlamPointCorrespondence message. Does not implicitly {@link bayesmech.vision.IdoSlamPointCorrespondence.verify|verify} messages.
             * @param message IdoSlamPointCorrespondence message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IIdoSlamPointCorrespondence, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified IdoSlamPointCorrespondence message, length delimited. Does not implicitly {@link bayesmech.vision.IdoSlamPointCorrespondence.verify|verify} messages.
             * @param message IdoSlamPointCorrespondence message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IIdoSlamPointCorrespondence, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes an IdoSlamPointCorrespondence message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns IdoSlamPointCorrespondence
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.IdoSlamPointCorrespondence;

            /**
             * Decodes an IdoSlamPointCorrespondence message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns IdoSlamPointCorrespondence
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.IdoSlamPointCorrespondence;

            /**
             * Verifies an IdoSlamPointCorrespondence message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates an IdoSlamPointCorrespondence message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns IdoSlamPointCorrespondence
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.IdoSlamPointCorrespondence;

            /**
             * Creates a plain object from an IdoSlamPointCorrespondence message. Also converts values to other types if specified.
             * @param message IdoSlamPointCorrespondence
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.IdoSlamPointCorrespondence, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this IdoSlamPointCorrespondence to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for IdoSlamPointCorrespondence
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of an IdoSlamPairDebug. */
        interface IIdoSlamPairDebug {

            /** IdoSlamPairDebug frameIndex */
            frameIndex?: (number|null);

            /** IdoSlamPairDebug pairedFrameIndex */
            pairedFrameIndex?: (number|null);

            /** IdoSlamPairDebug status */
            status?: (string|null);

            /** IdoSlamPairDebug goodMatchCount */
            goodMatchCount?: (number|null);

            /** IdoSlamPairDebug inlierCount */
            inlierCount?: (number|null);

            /** IdoSlamPairDebug triangulatedLeft */
            triangulatedLeft?: (number|null);

            /** IdoSlamPairDebug triangulatedRight */
            triangulatedRight?: (number|null);

            /** IdoSlamPairDebug onRoadCount */
            onRoadCount?: (number|null);

            /** IdoSlamPairDebug correspondences */
            correspondences?: (bayesmech.vision.IIdoSlamPointCorrespondence[]|null);
        }

        /** Represents an IdoSlamPairDebug. */
        class IdoSlamPairDebug implements IIdoSlamPairDebug {

            /**
             * Constructs a new IdoSlamPairDebug.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IIdoSlamPairDebug);

            /** IdoSlamPairDebug frameIndex. */
            public frameIndex: number;

            /** IdoSlamPairDebug pairedFrameIndex. */
            public pairedFrameIndex: number;

            /** IdoSlamPairDebug status. */
            public status: string;

            /** IdoSlamPairDebug goodMatchCount. */
            public goodMatchCount: number;

            /** IdoSlamPairDebug inlierCount. */
            public inlierCount: number;

            /** IdoSlamPairDebug triangulatedLeft. */
            public triangulatedLeft: number;

            /** IdoSlamPairDebug triangulatedRight. */
            public triangulatedRight: number;

            /** IdoSlamPairDebug onRoadCount. */
            public onRoadCount: number;

            /** IdoSlamPairDebug correspondences. */
            public correspondences: bayesmech.vision.IIdoSlamPointCorrespondence[];

            /**
             * Creates a new IdoSlamPairDebug instance using the specified properties.
             * @param [properties] Properties to set
             * @returns IdoSlamPairDebug instance
             */
            public static create(properties?: bayesmech.vision.IIdoSlamPairDebug): bayesmech.vision.IdoSlamPairDebug;

            /**
             * Encodes the specified IdoSlamPairDebug message. Does not implicitly {@link bayesmech.vision.IdoSlamPairDebug.verify|verify} messages.
             * @param message IdoSlamPairDebug message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IIdoSlamPairDebug, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified IdoSlamPairDebug message, length delimited. Does not implicitly {@link bayesmech.vision.IdoSlamPairDebug.verify|verify} messages.
             * @param message IdoSlamPairDebug message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IIdoSlamPairDebug, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes an IdoSlamPairDebug message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns IdoSlamPairDebug
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.IdoSlamPairDebug;

            /**
             * Decodes an IdoSlamPairDebug message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns IdoSlamPairDebug
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.IdoSlamPairDebug;

            /**
             * Verifies an IdoSlamPairDebug message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates an IdoSlamPairDebug message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns IdoSlamPairDebug
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.IdoSlamPairDebug;

            /**
             * Creates a plain object from an IdoSlamPairDebug message. Also converts values to other types if specified.
             * @param message IdoSlamPairDebug
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.IdoSlamPairDebug, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this IdoSlamPairDebug to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for IdoSlamPairDebug
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of an IdoSlamPairwiseMotion. */
        interface IIdoSlamPairwiseMotion {

            /** IdoSlamPairwiseMotion prevFrameIndex */
            prevFrameIndex?: (number|null);

            /** IdoSlamPairwiseMotion frameIndex */
            frameIndex?: (number|null);

            /** IdoSlamPairwiseMotion prevTimestampNs */
            prevTimestampNs?: (number|Long|null);

            /** IdoSlamPairwiseMotion timestampNs */
            timestampNs?: (number|Long|null);

            /** IdoSlamPairwiseMotion status */
            status?: (string|null);

            /** IdoSlamPairwiseMotion keypointsPrev */
            keypointsPrev?: (number|null);

            /** IdoSlamPairwiseMotion keypoints */
            keypoints?: (number|null);

            /** IdoSlamPairwiseMotion goodMatchCount */
            goodMatchCount?: (number|null);

            /** IdoSlamPairwiseMotion essentialInlierCount */
            essentialInlierCount?: (number|null);

            /** IdoSlamPairwiseMotion essentialInlierRatio */
            essentialInlierRatio?: (number|null);

            /** IdoSlamPairwiseMotion translationMagnitude */
            translationMagnitude?: (number|null);

            /** IdoSlamPairwiseMotion rotationDeg */
            rotationDeg?: (number|null);

            /** IdoSlamPairwiseMotion dx */
            dx?: (number|null);

            /** IdoSlamPairwiseMotion dy */
            dy?: (number|null);

            /** IdoSlamPairwiseMotion dz */
            dz?: (number|null);

            /** IdoSlamPairwiseMotion qx */
            qx?: (number|null);

            /** IdoSlamPairwiseMotion qy */
            qy?: (number|null);

            /** IdoSlamPairwiseMotion qz */
            qz?: (number|null);

            /** IdoSlamPairwiseMotion qw */
            qw?: (number|null);

            /** IdoSlamPairwiseMotion maskPixelsPrev */
            maskPixelsPrev?: (number|null);

            /** IdoSlamPairwiseMotion maskPixels */
            maskPixels?: (number|null);
        }

        /** Represents an IdoSlamPairwiseMotion. */
        class IdoSlamPairwiseMotion implements IIdoSlamPairwiseMotion {

            /**
             * Constructs a new IdoSlamPairwiseMotion.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IIdoSlamPairwiseMotion);

            /** IdoSlamPairwiseMotion prevFrameIndex. */
            public prevFrameIndex: number;

            /** IdoSlamPairwiseMotion frameIndex. */
            public frameIndex: number;

            /** IdoSlamPairwiseMotion prevTimestampNs. */
            public prevTimestampNs: (number|Long);

            /** IdoSlamPairwiseMotion timestampNs. */
            public timestampNs: (number|Long);

            /** IdoSlamPairwiseMotion status. */
            public status: string;

            /** IdoSlamPairwiseMotion keypointsPrev. */
            public keypointsPrev: number;

            /** IdoSlamPairwiseMotion keypoints. */
            public keypoints: number;

            /** IdoSlamPairwiseMotion goodMatchCount. */
            public goodMatchCount: number;

            /** IdoSlamPairwiseMotion essentialInlierCount. */
            public essentialInlierCount: number;

            /** IdoSlamPairwiseMotion essentialInlierRatio. */
            public essentialInlierRatio: number;

            /** IdoSlamPairwiseMotion translationMagnitude. */
            public translationMagnitude: number;

            /** IdoSlamPairwiseMotion rotationDeg. */
            public rotationDeg: number;

            /** IdoSlamPairwiseMotion dx. */
            public dx: number;

            /** IdoSlamPairwiseMotion dy. */
            public dy: number;

            /** IdoSlamPairwiseMotion dz. */
            public dz: number;

            /** IdoSlamPairwiseMotion qx. */
            public qx: number;

            /** IdoSlamPairwiseMotion qy. */
            public qy: number;

            /** IdoSlamPairwiseMotion qz. */
            public qz: number;

            /** IdoSlamPairwiseMotion qw. */
            public qw: number;

            /** IdoSlamPairwiseMotion maskPixelsPrev. */
            public maskPixelsPrev: number;

            /** IdoSlamPairwiseMotion maskPixels. */
            public maskPixels: number;

            /**
             * Creates a new IdoSlamPairwiseMotion instance using the specified properties.
             * @param [properties] Properties to set
             * @returns IdoSlamPairwiseMotion instance
             */
            public static create(properties?: bayesmech.vision.IIdoSlamPairwiseMotion): bayesmech.vision.IdoSlamPairwiseMotion;

            /**
             * Encodes the specified IdoSlamPairwiseMotion message. Does not implicitly {@link bayesmech.vision.IdoSlamPairwiseMotion.verify|verify} messages.
             * @param message IdoSlamPairwiseMotion message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IIdoSlamPairwiseMotion, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified IdoSlamPairwiseMotion message, length delimited. Does not implicitly {@link bayesmech.vision.IdoSlamPairwiseMotion.verify|verify} messages.
             * @param message IdoSlamPairwiseMotion message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IIdoSlamPairwiseMotion, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes an IdoSlamPairwiseMotion message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns IdoSlamPairwiseMotion
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.IdoSlamPairwiseMotion;

            /**
             * Decodes an IdoSlamPairwiseMotion message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns IdoSlamPairwiseMotion
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.IdoSlamPairwiseMotion;

            /**
             * Verifies an IdoSlamPairwiseMotion message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates an IdoSlamPairwiseMotion message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns IdoSlamPairwiseMotion
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.IdoSlamPairwiseMotion;

            /**
             * Creates a plain object from an IdoSlamPairwiseMotion message. Also converts values to other types if specified.
             * @param message IdoSlamPairwiseMotion
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.IdoSlamPairwiseMotion, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this IdoSlamPairwiseMotion to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for IdoSlamPairwiseMotion
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of an IdoSlamTrackWidthEstimate. */
        interface IIdoSlamTrackWidthEstimate {

            /** IdoSlamTrackWidthEstimate frameIndex */
            frameIndex?: (number|null);

            /** IdoSlamTrackWidthEstimate frameNumber */
            frameNumber?: (number|null);

            /** IdoSlamTrackWidthEstimate timestampNs */
            timestampNs?: (number|Long|null);

            /** IdoSlamTrackWidthEstimate latitude */
            latitude?: (number|null);

            /** IdoSlamTrackWidthEstimate longitude */
            longitude?: (number|null);

            /** IdoSlamTrackWidthEstimate widthM */
            widthM?: (number|null);

            /** IdoSlamTrackWidthEstimate leftOffsetM */
            leftOffsetM?: (number|null);

            /** IdoSlamTrackWidthEstimate rightOffsetM */
            rightOffsetM?: (number|null);

            /** IdoSlamTrackWidthEstimate bikeFraction */
            bikeFraction?: (number|null);

            /** IdoSlamTrackWidthEstimate method */
            method?: (string|null);
        }

        /** Represents an IdoSlamTrackWidthEstimate. */
        class IdoSlamTrackWidthEstimate implements IIdoSlamTrackWidthEstimate {

            /**
             * Constructs a new IdoSlamTrackWidthEstimate.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IIdoSlamTrackWidthEstimate);

            /** IdoSlamTrackWidthEstimate frameIndex. */
            public frameIndex: number;

            /** IdoSlamTrackWidthEstimate frameNumber. */
            public frameNumber: number;

            /** IdoSlamTrackWidthEstimate timestampNs. */
            public timestampNs: (number|Long);

            /** IdoSlamTrackWidthEstimate latitude. */
            public latitude: number;

            /** IdoSlamTrackWidthEstimate longitude. */
            public longitude: number;

            /** IdoSlamTrackWidthEstimate widthM. */
            public widthM: number;

            /** IdoSlamTrackWidthEstimate leftOffsetM. */
            public leftOffsetM: number;

            /** IdoSlamTrackWidthEstimate rightOffsetM. */
            public rightOffsetM: number;

            /** IdoSlamTrackWidthEstimate bikeFraction. */
            public bikeFraction: number;

            /** IdoSlamTrackWidthEstimate method. */
            public method: string;

            /**
             * Creates a new IdoSlamTrackWidthEstimate instance using the specified properties.
             * @param [properties] Properties to set
             * @returns IdoSlamTrackWidthEstimate instance
             */
            public static create(properties?: bayesmech.vision.IIdoSlamTrackWidthEstimate): bayesmech.vision.IdoSlamTrackWidthEstimate;

            /**
             * Encodes the specified IdoSlamTrackWidthEstimate message. Does not implicitly {@link bayesmech.vision.IdoSlamTrackWidthEstimate.verify|verify} messages.
             * @param message IdoSlamTrackWidthEstimate message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IIdoSlamTrackWidthEstimate, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified IdoSlamTrackWidthEstimate message, length delimited. Does not implicitly {@link bayesmech.vision.IdoSlamTrackWidthEstimate.verify|verify} messages.
             * @param message IdoSlamTrackWidthEstimate message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IIdoSlamTrackWidthEstimate, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes an IdoSlamTrackWidthEstimate message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns IdoSlamTrackWidthEstimate
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.IdoSlamTrackWidthEstimate;

            /**
             * Decodes an IdoSlamTrackWidthEstimate message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns IdoSlamTrackWidthEstimate
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.IdoSlamTrackWidthEstimate;

            /**
             * Verifies an IdoSlamTrackWidthEstimate message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates an IdoSlamTrackWidthEstimate message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns IdoSlamTrackWidthEstimate
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.IdoSlamTrackWidthEstimate;

            /**
             * Creates a plain object from an IdoSlamTrackWidthEstimate message. Also converts values to other types if specified.
             * @param message IdoSlamTrackWidthEstimate
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.IdoSlamTrackWidthEstimate, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this IdoSlamTrackWidthEstimate to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for IdoSlamTrackWidthEstimate
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of an IdoSlamCanonicalCenterlinePoint. */
        interface IIdoSlamCanonicalCenterlinePoint {

            /** IdoSlamCanonicalCenterlinePoint binIndex */
            binIndex?: (number|null);

            /** IdoSlamCanonicalCenterlinePoint progressM */
            progressM?: (number|null);

            /** IdoSlamCanonicalCenterlinePoint centerX */
            centerX?: (number|null);

            /** IdoSlamCanonicalCenterlinePoint centerY */
            centerY?: (number|null);

            /** IdoSlamCanonicalCenterlinePoint widthM */
            widthM?: (number|null);

            /** IdoSlamCanonicalCenterlinePoint leftX */
            leftX?: (number|null);

            /** IdoSlamCanonicalCenterlinePoint leftY */
            leftY?: (number|null);

            /** IdoSlamCanonicalCenterlinePoint rightX */
            rightX?: (number|null);

            /** IdoSlamCanonicalCenterlinePoint rightY */
            rightY?: (number|null);
        }

        /** Represents an IdoSlamCanonicalCenterlinePoint. */
        class IdoSlamCanonicalCenterlinePoint implements IIdoSlamCanonicalCenterlinePoint {

            /**
             * Constructs a new IdoSlamCanonicalCenterlinePoint.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IIdoSlamCanonicalCenterlinePoint);

            /** IdoSlamCanonicalCenterlinePoint binIndex. */
            public binIndex: number;

            /** IdoSlamCanonicalCenterlinePoint progressM. */
            public progressM: number;

            /** IdoSlamCanonicalCenterlinePoint centerX. */
            public centerX: number;

            /** IdoSlamCanonicalCenterlinePoint centerY. */
            public centerY: number;

            /** IdoSlamCanonicalCenterlinePoint widthM. */
            public widthM: number;

            /** IdoSlamCanonicalCenterlinePoint leftX. */
            public leftX: number;

            /** IdoSlamCanonicalCenterlinePoint leftY. */
            public leftY: number;

            /** IdoSlamCanonicalCenterlinePoint rightX. */
            public rightX: number;

            /** IdoSlamCanonicalCenterlinePoint rightY. */
            public rightY: number;

            /**
             * Creates a new IdoSlamCanonicalCenterlinePoint instance using the specified properties.
             * @param [properties] Properties to set
             * @returns IdoSlamCanonicalCenterlinePoint instance
             */
            public static create(properties?: bayesmech.vision.IIdoSlamCanonicalCenterlinePoint): bayesmech.vision.IdoSlamCanonicalCenterlinePoint;

            /**
             * Encodes the specified IdoSlamCanonicalCenterlinePoint message. Does not implicitly {@link bayesmech.vision.IdoSlamCanonicalCenterlinePoint.verify|verify} messages.
             * @param message IdoSlamCanonicalCenterlinePoint message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IIdoSlamCanonicalCenterlinePoint, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified IdoSlamCanonicalCenterlinePoint message, length delimited. Does not implicitly {@link bayesmech.vision.IdoSlamCanonicalCenterlinePoint.verify|verify} messages.
             * @param message IdoSlamCanonicalCenterlinePoint message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IIdoSlamCanonicalCenterlinePoint, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes an IdoSlamCanonicalCenterlinePoint message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns IdoSlamCanonicalCenterlinePoint
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.IdoSlamCanonicalCenterlinePoint;

            /**
             * Decodes an IdoSlamCanonicalCenterlinePoint message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns IdoSlamCanonicalCenterlinePoint
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.IdoSlamCanonicalCenterlinePoint;

            /**
             * Verifies an IdoSlamCanonicalCenterlinePoint message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates an IdoSlamCanonicalCenterlinePoint message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns IdoSlamCanonicalCenterlinePoint
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.IdoSlamCanonicalCenterlinePoint;

            /**
             * Creates a plain object from an IdoSlamCanonicalCenterlinePoint message. Also converts values to other types if specified.
             * @param message IdoSlamCanonicalCenterlinePoint
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.IdoSlamCanonicalCenterlinePoint, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this IdoSlamCanonicalCenterlinePoint to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for IdoSlamCanonicalCenterlinePoint
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of an IdoSlamCanonicalFrameTrack. */
        interface IIdoSlamCanonicalFrameTrack {

            /** IdoSlamCanonicalFrameTrack frameIndex */
            frameIndex?: (number|null);

            /** IdoSlamCanonicalFrameTrack frameNumber */
            frameNumber?: (number|null);

            /** IdoSlamCanonicalFrameTrack timestampNs */
            timestampNs?: (number|Long|null);

            /** IdoSlamCanonicalFrameTrack lapId */
            lapId?: (number|null);

            /** IdoSlamCanonicalFrameTrack isPartialLap */
            isPartialLap?: (boolean|null);

            /** IdoSlamCanonicalFrameTrack progressM */
            progressM?: (number|null);

            /** IdoSlamCanonicalFrameTrack progressFraction */
            progressFraction?: (number|null);

            /** IdoSlamCanonicalFrameTrack gpsX */
            gpsX?: (number|null);

            /** IdoSlamCanonicalFrameTrack gpsY */
            gpsY?: (number|null);

            /** IdoSlamCanonicalFrameTrack canonicalX */
            canonicalX?: (number|null);

            /** IdoSlamCanonicalFrameTrack canonicalY */
            canonicalY?: (number|null);

            /** IdoSlamCanonicalFrameTrack lateralOffsetM */
            lateralOffsetM?: (number|null);

            /** IdoSlamCanonicalFrameTrack imageLateralM */
            imageLateralM?: (number|null);

            /** IdoSlamCanonicalFrameTrack hasImageLateralM */
            hasImageLateralM?: (boolean|null);

            /** IdoSlamCanonicalFrameTrack trajectoryLateralM */
            trajectoryLateralM?: (number|null);

            /** IdoSlamCanonicalFrameTrack trajectoryX */
            trajectoryX?: (number|null);

            /** IdoSlamCanonicalFrameTrack trajectoryY */
            trajectoryY?: (number|null);

            /** IdoSlamCanonicalFrameTrack widthM */
            widthM?: (number|null);

            /** IdoSlamCanonicalFrameTrack halfWidthM */
            halfWidthM?: (number|null);
        }

        /** Represents an IdoSlamCanonicalFrameTrack. */
        class IdoSlamCanonicalFrameTrack implements IIdoSlamCanonicalFrameTrack {

            /**
             * Constructs a new IdoSlamCanonicalFrameTrack.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IIdoSlamCanonicalFrameTrack);

            /** IdoSlamCanonicalFrameTrack frameIndex. */
            public frameIndex: number;

            /** IdoSlamCanonicalFrameTrack frameNumber. */
            public frameNumber: number;

            /** IdoSlamCanonicalFrameTrack timestampNs. */
            public timestampNs: (number|Long);

            /** IdoSlamCanonicalFrameTrack lapId. */
            public lapId: number;

            /** IdoSlamCanonicalFrameTrack isPartialLap. */
            public isPartialLap: boolean;

            /** IdoSlamCanonicalFrameTrack progressM. */
            public progressM: number;

            /** IdoSlamCanonicalFrameTrack progressFraction. */
            public progressFraction: number;

            /** IdoSlamCanonicalFrameTrack gpsX. */
            public gpsX: number;

            /** IdoSlamCanonicalFrameTrack gpsY. */
            public gpsY: number;

            /** IdoSlamCanonicalFrameTrack canonicalX. */
            public canonicalX: number;

            /** IdoSlamCanonicalFrameTrack canonicalY. */
            public canonicalY: number;

            /** IdoSlamCanonicalFrameTrack lateralOffsetM. */
            public lateralOffsetM: number;

            /** IdoSlamCanonicalFrameTrack imageLateralM. */
            public imageLateralM: number;

            /** IdoSlamCanonicalFrameTrack hasImageLateralM. */
            public hasImageLateralM: boolean;

            /** IdoSlamCanonicalFrameTrack trajectoryLateralM. */
            public trajectoryLateralM: number;

            /** IdoSlamCanonicalFrameTrack trajectoryX. */
            public trajectoryX: number;

            /** IdoSlamCanonicalFrameTrack trajectoryY. */
            public trajectoryY: number;

            /** IdoSlamCanonicalFrameTrack widthM. */
            public widthM: number;

            /** IdoSlamCanonicalFrameTrack halfWidthM. */
            public halfWidthM: number;

            /**
             * Creates a new IdoSlamCanonicalFrameTrack instance using the specified properties.
             * @param [properties] Properties to set
             * @returns IdoSlamCanonicalFrameTrack instance
             */
            public static create(properties?: bayesmech.vision.IIdoSlamCanonicalFrameTrack): bayesmech.vision.IdoSlamCanonicalFrameTrack;

            /**
             * Encodes the specified IdoSlamCanonicalFrameTrack message. Does not implicitly {@link bayesmech.vision.IdoSlamCanonicalFrameTrack.verify|verify} messages.
             * @param message IdoSlamCanonicalFrameTrack message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IIdoSlamCanonicalFrameTrack, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified IdoSlamCanonicalFrameTrack message, length delimited. Does not implicitly {@link bayesmech.vision.IdoSlamCanonicalFrameTrack.verify|verify} messages.
             * @param message IdoSlamCanonicalFrameTrack message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IIdoSlamCanonicalFrameTrack, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes an IdoSlamCanonicalFrameTrack message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns IdoSlamCanonicalFrameTrack
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.IdoSlamCanonicalFrameTrack;

            /**
             * Decodes an IdoSlamCanonicalFrameTrack message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns IdoSlamCanonicalFrameTrack
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.IdoSlamCanonicalFrameTrack;

            /**
             * Verifies an IdoSlamCanonicalFrameTrack message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates an IdoSlamCanonicalFrameTrack message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns IdoSlamCanonicalFrameTrack
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.IdoSlamCanonicalFrameTrack;

            /**
             * Creates a plain object from an IdoSlamCanonicalFrameTrack message. Also converts values to other types if specified.
             * @param message IdoSlamCanonicalFrameTrack
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.IdoSlamCanonicalFrameTrack, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this IdoSlamCanonicalFrameTrack to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for IdoSlamCanonicalFrameTrack
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of an IdoSlamResponse. */
        interface IIdoSlamResponse {

            /** IdoSlamResponse firstFrameId */
            firstFrameId?: (bayesmech.vision.IPerceiverFrameIdentifier|null);

            /** IdoSlamResponse recordingPath */
            recordingPath?: (string|null);

            /** IdoSlamResponse segmentationPath */
            segmentationPath?: (string|null);

            /** IdoSlamResponse workspacePath */
            workspacePath?: (string|null);

            /** IdoSlamResponse framePoses */
            framePoses?: (bayesmech.vision.IIdoSlamFramePose[]|null);

            /** IdoSlamResponse groundPoints */
            groundPoints?: (bayesmech.vision.IIdoSlamGroundPoint[]|null);

            /** IdoSlamResponse pairDebug */
            pairDebug?: (bayesmech.vision.IIdoSlamPairDebug[]|null);

            /** IdoSlamResponse refinedFramePoses */
            refinedFramePoses?: (bayesmech.vision.IIdoSlamFramePose[]|null);

            /** IdoSlamResponse pairwiseMotion */
            pairwiseMotion?: (bayesmech.vision.IIdoSlamPairwiseMotion[]|null);

            /** IdoSlamResponse planeWidthEstimates */
            planeWidthEstimates?: (bayesmech.vision.IIdoSlamTrackWidthEstimate[]|null);

            /** IdoSlamResponse planeWidthSummaryJson */
            planeWidthSummaryJson?: (string|null);

            /** IdoSlamResponse canonicalCenterline */
            canonicalCenterline?: (bayesmech.vision.IIdoSlamCanonicalCenterlinePoint[]|null);

            /** IdoSlamResponse canonicalFrameTracks */
            canonicalFrameTracks?: (bayesmech.vision.IIdoSlamCanonicalFrameTrack[]|null);

            /** IdoSlamResponse canonicalSummaryJson */
            canonicalSummaryJson?: (string|null);

            /** IdoSlamResponse triangulatedWidthEstimates */
            triangulatedWidthEstimates?: (bayesmech.vision.IIdoSlamTrackWidthEstimate[]|null);

            /** IdoSlamResponse triangulatedSummaryJson */
            triangulatedSummaryJson?: (string|null);
        }

        /** Represents an IdoSlamResponse. */
        class IdoSlamResponse implements IIdoSlamResponse {

            /**
             * Constructs a new IdoSlamResponse.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IIdoSlamResponse);

            /** IdoSlamResponse firstFrameId. */
            public firstFrameId?: (bayesmech.vision.IPerceiverFrameIdentifier|null);

            /** IdoSlamResponse recordingPath. */
            public recordingPath: string;

            /** IdoSlamResponse segmentationPath. */
            public segmentationPath: string;

            /** IdoSlamResponse workspacePath. */
            public workspacePath: string;

            /** IdoSlamResponse framePoses. */
            public framePoses: bayesmech.vision.IIdoSlamFramePose[];

            /** IdoSlamResponse groundPoints. */
            public groundPoints: bayesmech.vision.IIdoSlamGroundPoint[];

            /** IdoSlamResponse pairDebug. */
            public pairDebug: bayesmech.vision.IIdoSlamPairDebug[];

            /** IdoSlamResponse refinedFramePoses. */
            public refinedFramePoses: bayesmech.vision.IIdoSlamFramePose[];

            /** IdoSlamResponse pairwiseMotion. */
            public pairwiseMotion: bayesmech.vision.IIdoSlamPairwiseMotion[];

            /** IdoSlamResponse planeWidthEstimates. */
            public planeWidthEstimates: bayesmech.vision.IIdoSlamTrackWidthEstimate[];

            /** IdoSlamResponse planeWidthSummaryJson. */
            public planeWidthSummaryJson: string;

            /** IdoSlamResponse canonicalCenterline. */
            public canonicalCenterline: bayesmech.vision.IIdoSlamCanonicalCenterlinePoint[];

            /** IdoSlamResponse canonicalFrameTracks. */
            public canonicalFrameTracks: bayesmech.vision.IIdoSlamCanonicalFrameTrack[];

            /** IdoSlamResponse canonicalSummaryJson. */
            public canonicalSummaryJson: string;

            /** IdoSlamResponse triangulatedWidthEstimates. */
            public triangulatedWidthEstimates: bayesmech.vision.IIdoSlamTrackWidthEstimate[];

            /** IdoSlamResponse triangulatedSummaryJson. */
            public triangulatedSummaryJson: string;

            /**
             * Creates a new IdoSlamResponse instance using the specified properties.
             * @param [properties] Properties to set
             * @returns IdoSlamResponse instance
             */
            public static create(properties?: bayesmech.vision.IIdoSlamResponse): bayesmech.vision.IdoSlamResponse;

            /**
             * Encodes the specified IdoSlamResponse message. Does not implicitly {@link bayesmech.vision.IdoSlamResponse.verify|verify} messages.
             * @param message IdoSlamResponse message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IIdoSlamResponse, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified IdoSlamResponse message, length delimited. Does not implicitly {@link bayesmech.vision.IdoSlamResponse.verify|verify} messages.
             * @param message IdoSlamResponse message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IIdoSlamResponse, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes an IdoSlamResponse message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns IdoSlamResponse
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.IdoSlamResponse;

            /**
             * Decodes an IdoSlamResponse message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns IdoSlamResponse
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.IdoSlamResponse;

            /**
             * Verifies an IdoSlamResponse message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates an IdoSlamResponse message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns IdoSlamResponse
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.IdoSlamResponse;

            /**
             * Creates a plain object from an IdoSlamResponse message. Also converts values to other types if specified.
             * @param message IdoSlamResponse
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.IdoSlamResponse, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this IdoSlamResponse to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for IdoSlamResponse
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        /** Properties of a PongtownResponse. */
        interface IPongtownResponse {

            /** PongtownResponse frameIdentifier */
            frameIdentifier?: (bayesmech.vision.IPerceiverFrameIdentifier|null);

            /** PongtownResponse tablePose */
            tablePose?: (bayesmech.vision.PongtownResponse.ITablePose|null);

            /** PongtownResponse pnpFrameDebug */
            pnpFrameDebug?: (bayesmech.vision.PongtownResponse.IPnpFrameDebug[]|null);

            /** PongtownResponse frameOutput */
            frameOutput?: (bayesmech.vision.PongtownResponse.IFrameOutput|null);

            /** PongtownResponse globalTablePose */
            globalTablePose?: (bayesmech.vision.PongtownResponse.IGlobalTablePose|null);

            /** PongtownResponse tableWidthMm */
            tableWidthMm?: (number|null);

            /** PongtownResponse tableHeightMm */
            tableHeightMm?: (number|null);

            /** PongtownResponse netOverhangMm */
            netOverhangMm?: (number|null);

            /** PongtownResponse netHeightMm */
            netHeightMm?: (number|null);
        }

        /** Represents a PongtownResponse. */
        class PongtownResponse implements IPongtownResponse {

            /**
             * Constructs a new PongtownResponse.
             * @param [properties] Properties to set
             */
            constructor(properties?: bayesmech.vision.IPongtownResponse);

            /** PongtownResponse frameIdentifier. */
            public frameIdentifier?: (bayesmech.vision.IPerceiverFrameIdentifier|null);

            /** PongtownResponse tablePose. */
            public tablePose?: (bayesmech.vision.PongtownResponse.ITablePose|null);

            /** PongtownResponse pnpFrameDebug. */
            public pnpFrameDebug: bayesmech.vision.PongtownResponse.IPnpFrameDebug[];

            /** PongtownResponse frameOutput. */
            public frameOutput?: (bayesmech.vision.PongtownResponse.IFrameOutput|null);

            /** PongtownResponse globalTablePose. */
            public globalTablePose?: (bayesmech.vision.PongtownResponse.IGlobalTablePose|null);

            /** PongtownResponse tableWidthMm. */
            public tableWidthMm: number;

            /** PongtownResponse tableHeightMm. */
            public tableHeightMm: number;

            /** PongtownResponse netOverhangMm. */
            public netOverhangMm: number;

            /** PongtownResponse netHeightMm. */
            public netHeightMm: number;

            /**
             * Creates a new PongtownResponse instance using the specified properties.
             * @param [properties] Properties to set
             * @returns PongtownResponse instance
             */
            public static create(properties?: bayesmech.vision.IPongtownResponse): bayesmech.vision.PongtownResponse;

            /**
             * Encodes the specified PongtownResponse message. Does not implicitly {@link bayesmech.vision.PongtownResponse.verify|verify} messages.
             * @param message PongtownResponse message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encode(message: bayesmech.vision.IPongtownResponse, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Encodes the specified PongtownResponse message, length delimited. Does not implicitly {@link bayesmech.vision.PongtownResponse.verify|verify} messages.
             * @param message PongtownResponse message or plain object to encode
             * @param [writer] Writer to encode to
             * @returns Writer
             */
            public static encodeDelimited(message: bayesmech.vision.IPongtownResponse, writer?: $protobuf.Writer): $protobuf.Writer;

            /**
             * Decodes a PongtownResponse message from the specified reader or buffer.
             * @param reader Reader or buffer to decode from
             * @param [length] Message length if known beforehand
             * @returns PongtownResponse
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.PongtownResponse;

            /**
             * Decodes a PongtownResponse message from the specified reader or buffer, length delimited.
             * @param reader Reader or buffer to decode from
             * @returns PongtownResponse
             * @throws {Error} If the payload is not a reader or valid buffer
             * @throws {$protobuf.util.ProtocolError} If required fields are missing
             */
            public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.PongtownResponse;

            /**
             * Verifies a PongtownResponse message.
             * @param message Plain object to verify
             * @returns `null` if valid, otherwise the reason why it is not
             */
            public static verify(message: { [k: string]: any }): (string|null);

            /**
             * Creates a PongtownResponse message from a plain object. Also converts values to their respective internal types.
             * @param object Plain object
             * @returns PongtownResponse
             */
            public static fromObject(object: { [k: string]: any }): bayesmech.vision.PongtownResponse;

            /**
             * Creates a plain object from a PongtownResponse message. Also converts values to other types if specified.
             * @param message PongtownResponse
             * @param [options] Conversion options
             * @returns Plain object
             */
            public static toObject(message: bayesmech.vision.PongtownResponse, options?: $protobuf.IConversionOptions): { [k: string]: any };

            /**
             * Converts this PongtownResponse to JSON.
             * @returns JSON object
             */
            public toJSON(): { [k: string]: any };

            /**
             * Gets the default type url for PongtownResponse
             * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
             * @returns The default type url
             */
            public static getTypeUrl(typeUrlPrefix?: string): string;
        }

        namespace PongtownResponse {

            /** Properties of a TablePose. */
            interface ITablePose {

                /** TablePose method */
                method?: (bayesmech.vision.PongtownResponse.TablePose.Method|null);

                /** TablePose quadQuality */
                quadQuality?: (number|null);

                /** TablePose pnpIou */
                pnpIou?: (number|null);

                /** TablePose quadImg */
                quadImg?: (number[]|null);

                /** TablePose midlineImg */
                midlineImg?: (number[]|null);

                /** TablePose TTableToCamera */
                TTableToCamera?: (number[]|null);

                /** TablePose quadImgGlobal */
                quadImgGlobal?: (number[]|null);

                /** TablePose globalIou */
                globalIou?: (number|null);
            }

            /** Represents a TablePose. */
            class TablePose implements ITablePose {

                /**
                 * Constructs a new TablePose.
                 * @param [properties] Properties to set
                 */
                constructor(properties?: bayesmech.vision.PongtownResponse.ITablePose);

                /** TablePose method. */
                public method: bayesmech.vision.PongtownResponse.TablePose.Method;

                /** TablePose quadQuality. */
                public quadQuality: number;

                /** TablePose pnpIou. */
                public pnpIou: number;

                /** TablePose quadImg. */
                public quadImg: number[];

                /** TablePose midlineImg. */
                public midlineImg: number[];

                /** TablePose TTableToCamera. */
                public TTableToCamera: number[];

                /** TablePose quadImgGlobal. */
                public quadImgGlobal: number[];

                /** TablePose globalIou. */
                public globalIou: number;

                /**
                 * Creates a new TablePose instance using the specified properties.
                 * @param [properties] Properties to set
                 * @returns TablePose instance
                 */
                public static create(properties?: bayesmech.vision.PongtownResponse.ITablePose): bayesmech.vision.PongtownResponse.TablePose;

                /**
                 * Encodes the specified TablePose message. Does not implicitly {@link bayesmech.vision.PongtownResponse.TablePose.verify|verify} messages.
                 * @param message TablePose message or plain object to encode
                 * @param [writer] Writer to encode to
                 * @returns Writer
                 */
                public static encode(message: bayesmech.vision.PongtownResponse.ITablePose, writer?: $protobuf.Writer): $protobuf.Writer;

                /**
                 * Encodes the specified TablePose message, length delimited. Does not implicitly {@link bayesmech.vision.PongtownResponse.TablePose.verify|verify} messages.
                 * @param message TablePose message or plain object to encode
                 * @param [writer] Writer to encode to
                 * @returns Writer
                 */
                public static encodeDelimited(message: bayesmech.vision.PongtownResponse.ITablePose, writer?: $protobuf.Writer): $protobuf.Writer;

                /**
                 * Decodes a TablePose message from the specified reader or buffer.
                 * @param reader Reader or buffer to decode from
                 * @param [length] Message length if known beforehand
                 * @returns TablePose
                 * @throws {Error} If the payload is not a reader or valid buffer
                 * @throws {$protobuf.util.ProtocolError} If required fields are missing
                 */
                public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.PongtownResponse.TablePose;

                /**
                 * Decodes a TablePose message from the specified reader or buffer, length delimited.
                 * @param reader Reader or buffer to decode from
                 * @returns TablePose
                 * @throws {Error} If the payload is not a reader or valid buffer
                 * @throws {$protobuf.util.ProtocolError} If required fields are missing
                 */
                public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.PongtownResponse.TablePose;

                /**
                 * Verifies a TablePose message.
                 * @param message Plain object to verify
                 * @returns `null` if valid, otherwise the reason why it is not
                 */
                public static verify(message: { [k: string]: any }): (string|null);

                /**
                 * Creates a TablePose message from a plain object. Also converts values to their respective internal types.
                 * @param object Plain object
                 * @returns TablePose
                 */
                public static fromObject(object: { [k: string]: any }): bayesmech.vision.PongtownResponse.TablePose;

                /**
                 * Creates a plain object from a TablePose message. Also converts values to other types if specified.
                 * @param message TablePose
                 * @param [options] Conversion options
                 * @returns Plain object
                 */
                public static toObject(message: bayesmech.vision.PongtownResponse.TablePose, options?: $protobuf.IConversionOptions): { [k: string]: any };

                /**
                 * Converts this TablePose to JSON.
                 * @returns JSON object
                 */
                public toJSON(): { [k: string]: any };

                /**
                 * Gets the default type url for TablePose
                 * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
                 * @returns The default type url
                 */
                public static getTypeUrl(typeUrlPrefix?: string): string;
            }

            namespace TablePose {

                /** Method enum. */
                enum Method {
                    UNKNOWN = 0,
                    QUAD_FULL = 1,
                    QUAD_FROM_MIDLINE = 2,
                    QUAD_FAILED = 3,
                    OFF_SCREEN = 4
                }
            }

            /** Properties of a PnpFrameDebug. */
            interface IPnpFrameDebug {

                /** PnpFrameDebug frameIdx */
                frameIdx?: (number|null);

                /** PnpFrameDebug cameraIntrinsics */
                cameraIntrinsics?: (bayesmech.vision.ICameraIntrinsics|null);

                /** PnpFrameDebug cameraMatrix */
                cameraMatrix?: (number[]|null);

                /** PnpFrameDebug imagePlaneMethod */
                imagePlaneMethod?: (bayesmech.vision.PongtownResponse.TablePose.Method|null);

                /** PnpFrameDebug imagePlaneQuadQuality */
                imagePlaneQuadQuality?: (number|null);

                /** PnpFrameDebug imagePlaneTableQuadImg */
                imagePlaneTableQuadImg?: (number[]|null);

                /** PnpFrameDebug imagePlaneHalfTableQuadImg */
                imagePlaneHalfTableQuadImg?: (number[]|null);

                /** PnpFrameDebug imagePlaneMidlineImg */
                imagePlaneMidlineImg?: (number[]|null);

                /** PnpFrameDebug imagePlaneNetQuadImg */
                imagePlaneNetQuadImg?: (number[]|null);

                /** PnpFrameDebug pnpTableSuccess */
                pnpTableSuccess?: (boolean|null);

                /** PnpFrameDebug pnpTableIou */
                pnpTableIou?: (number|null);

                /** PnpFrameDebug pnpTableQuadImg */
                pnpTableQuadImg?: (number[]|null);

                /** PnpFrameDebug pnp_TTableToCamera */
                pnp_TTableToCamera?: (number[]|null);

                /** PnpFrameDebug pnpNetSuccess */
                pnpNetSuccess?: (boolean|null);

                /** PnpFrameDebug pnpNetIou */
                pnpNetIou?: (number|null);

                /** PnpFrameDebug pnpNetQuadImg */
                pnpNetQuadImg?: (number[]|null);

                /** PnpFrameDebug pnp_TNetToCamera */
                pnp_TNetToCamera?: (number[]|null);

                /** PnpFrameDebug pnpOverlayNetQuadImg */
                pnpOverlayNetQuadImg?: (number[]|null);
            }

            /** Represents a PnpFrameDebug. */
            class PnpFrameDebug implements IPnpFrameDebug {

                /**
                 * Constructs a new PnpFrameDebug.
                 * @param [properties] Properties to set
                 */
                constructor(properties?: bayesmech.vision.PongtownResponse.IPnpFrameDebug);

                /** PnpFrameDebug frameIdx. */
                public frameIdx: number;

                /** PnpFrameDebug cameraIntrinsics. */
                public cameraIntrinsics?: (bayesmech.vision.ICameraIntrinsics|null);

                /** PnpFrameDebug cameraMatrix. */
                public cameraMatrix: number[];

                /** PnpFrameDebug imagePlaneMethod. */
                public imagePlaneMethod: bayesmech.vision.PongtownResponse.TablePose.Method;

                /** PnpFrameDebug imagePlaneQuadQuality. */
                public imagePlaneQuadQuality: number;

                /** PnpFrameDebug imagePlaneTableQuadImg. */
                public imagePlaneTableQuadImg: number[];

                /** PnpFrameDebug imagePlaneHalfTableQuadImg. */
                public imagePlaneHalfTableQuadImg: number[];

                /** PnpFrameDebug imagePlaneMidlineImg. */
                public imagePlaneMidlineImg: number[];

                /** PnpFrameDebug imagePlaneNetQuadImg. */
                public imagePlaneNetQuadImg: number[];

                /** PnpFrameDebug pnpTableSuccess. */
                public pnpTableSuccess: boolean;

                /** PnpFrameDebug pnpTableIou. */
                public pnpTableIou: number;

                /** PnpFrameDebug pnpTableQuadImg. */
                public pnpTableQuadImg: number[];

                /** PnpFrameDebug pnp_TTableToCamera. */
                public pnp_TTableToCamera: number[];

                /** PnpFrameDebug pnpNetSuccess. */
                public pnpNetSuccess: boolean;

                /** PnpFrameDebug pnpNetIou. */
                public pnpNetIou: number;

                /** PnpFrameDebug pnpNetQuadImg. */
                public pnpNetQuadImg: number[];

                /** PnpFrameDebug pnp_TNetToCamera. */
                public pnp_TNetToCamera: number[];

                /** PnpFrameDebug pnpOverlayNetQuadImg. */
                public pnpOverlayNetQuadImg: number[];

                /**
                 * Creates a new PnpFrameDebug instance using the specified properties.
                 * @param [properties] Properties to set
                 * @returns PnpFrameDebug instance
                 */
                public static create(properties?: bayesmech.vision.PongtownResponse.IPnpFrameDebug): bayesmech.vision.PongtownResponse.PnpFrameDebug;

                /**
                 * Encodes the specified PnpFrameDebug message. Does not implicitly {@link bayesmech.vision.PongtownResponse.PnpFrameDebug.verify|verify} messages.
                 * @param message PnpFrameDebug message or plain object to encode
                 * @param [writer] Writer to encode to
                 * @returns Writer
                 */
                public static encode(message: bayesmech.vision.PongtownResponse.IPnpFrameDebug, writer?: $protobuf.Writer): $protobuf.Writer;

                /**
                 * Encodes the specified PnpFrameDebug message, length delimited. Does not implicitly {@link bayesmech.vision.PongtownResponse.PnpFrameDebug.verify|verify} messages.
                 * @param message PnpFrameDebug message or plain object to encode
                 * @param [writer] Writer to encode to
                 * @returns Writer
                 */
                public static encodeDelimited(message: bayesmech.vision.PongtownResponse.IPnpFrameDebug, writer?: $protobuf.Writer): $protobuf.Writer;

                /**
                 * Decodes a PnpFrameDebug message from the specified reader or buffer.
                 * @param reader Reader or buffer to decode from
                 * @param [length] Message length if known beforehand
                 * @returns PnpFrameDebug
                 * @throws {Error} If the payload is not a reader or valid buffer
                 * @throws {$protobuf.util.ProtocolError} If required fields are missing
                 */
                public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.PongtownResponse.PnpFrameDebug;

                /**
                 * Decodes a PnpFrameDebug message from the specified reader or buffer, length delimited.
                 * @param reader Reader or buffer to decode from
                 * @returns PnpFrameDebug
                 * @throws {Error} If the payload is not a reader or valid buffer
                 * @throws {$protobuf.util.ProtocolError} If required fields are missing
                 */
                public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.PongtownResponse.PnpFrameDebug;

                /**
                 * Verifies a PnpFrameDebug message.
                 * @param message Plain object to verify
                 * @returns `null` if valid, otherwise the reason why it is not
                 */
                public static verify(message: { [k: string]: any }): (string|null);

                /**
                 * Creates a PnpFrameDebug message from a plain object. Also converts values to their respective internal types.
                 * @param object Plain object
                 * @returns PnpFrameDebug
                 */
                public static fromObject(object: { [k: string]: any }): bayesmech.vision.PongtownResponse.PnpFrameDebug;

                /**
                 * Creates a plain object from a PnpFrameDebug message. Also converts values to other types if specified.
                 * @param message PnpFrameDebug
                 * @param [options] Conversion options
                 * @returns Plain object
                 */
                public static toObject(message: bayesmech.vision.PongtownResponse.PnpFrameDebug, options?: $protobuf.IConversionOptions): { [k: string]: any };

                /**
                 * Converts this PnpFrameDebug to JSON.
                 * @returns JSON object
                 */
                public toJSON(): { [k: string]: any };

                /**
                 * Gets the default type url for PnpFrameDebug
                 * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
                 * @returns The default type url
                 */
                public static getTypeUrl(typeUrlPrefix?: string): string;
            }

            /** Properties of a FrameOutput. */
            interface IFrameOutput {

                /** FrameOutput frameIdx */
                frameIdx?: (number|null);

                /** FrameOutput hasPose */
                hasPose?: (boolean|null);

                /** FrameOutput offScreen */
                offScreen?: (boolean|null);

                /** FrameOutput globalIou */
                globalIou?: (number|null);

                /** FrameOutput TTableToCamera */
                TTableToCamera?: (number[]|null);

                /** FrameOutput tableQuadImg */
                tableQuadImg?: (number[]|null);

                /** FrameOutput netQuadImg */
                netQuadImg?: (number[]|null);

                /** FrameOutput hasNetPose */
                hasNetPose?: (boolean|null);

                /** FrameOutput TNetToCamera */
                TNetToCamera?: (number[]|null);
            }

            /** Represents a FrameOutput. */
            class FrameOutput implements IFrameOutput {

                /**
                 * Constructs a new FrameOutput.
                 * @param [properties] Properties to set
                 */
                constructor(properties?: bayesmech.vision.PongtownResponse.IFrameOutput);

                /** FrameOutput frameIdx. */
                public frameIdx: number;

                /** FrameOutput hasPose. */
                public hasPose: boolean;

                /** FrameOutput offScreen. */
                public offScreen: boolean;

                /** FrameOutput globalIou. */
                public globalIou: number;

                /** FrameOutput TTableToCamera. */
                public TTableToCamera: number[];

                /** FrameOutput tableQuadImg. */
                public tableQuadImg: number[];

                /** FrameOutput netQuadImg. */
                public netQuadImg: number[];

                /** FrameOutput hasNetPose. */
                public hasNetPose: boolean;

                /** FrameOutput TNetToCamera. */
                public TNetToCamera: number[];

                /**
                 * Creates a new FrameOutput instance using the specified properties.
                 * @param [properties] Properties to set
                 * @returns FrameOutput instance
                 */
                public static create(properties?: bayesmech.vision.PongtownResponse.IFrameOutput): bayesmech.vision.PongtownResponse.FrameOutput;

                /**
                 * Encodes the specified FrameOutput message. Does not implicitly {@link bayesmech.vision.PongtownResponse.FrameOutput.verify|verify} messages.
                 * @param message FrameOutput message or plain object to encode
                 * @param [writer] Writer to encode to
                 * @returns Writer
                 */
                public static encode(message: bayesmech.vision.PongtownResponse.IFrameOutput, writer?: $protobuf.Writer): $protobuf.Writer;

                /**
                 * Encodes the specified FrameOutput message, length delimited. Does not implicitly {@link bayesmech.vision.PongtownResponse.FrameOutput.verify|verify} messages.
                 * @param message FrameOutput message or plain object to encode
                 * @param [writer] Writer to encode to
                 * @returns Writer
                 */
                public static encodeDelimited(message: bayesmech.vision.PongtownResponse.IFrameOutput, writer?: $protobuf.Writer): $protobuf.Writer;

                /**
                 * Decodes a FrameOutput message from the specified reader or buffer.
                 * @param reader Reader or buffer to decode from
                 * @param [length] Message length if known beforehand
                 * @returns FrameOutput
                 * @throws {Error} If the payload is not a reader or valid buffer
                 * @throws {$protobuf.util.ProtocolError} If required fields are missing
                 */
                public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.PongtownResponse.FrameOutput;

                /**
                 * Decodes a FrameOutput message from the specified reader or buffer, length delimited.
                 * @param reader Reader or buffer to decode from
                 * @returns FrameOutput
                 * @throws {Error} If the payload is not a reader or valid buffer
                 * @throws {$protobuf.util.ProtocolError} If required fields are missing
                 */
                public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.PongtownResponse.FrameOutput;

                /**
                 * Verifies a FrameOutput message.
                 * @param message Plain object to verify
                 * @returns `null` if valid, otherwise the reason why it is not
                 */
                public static verify(message: { [k: string]: any }): (string|null);

                /**
                 * Creates a FrameOutput message from a plain object. Also converts values to their respective internal types.
                 * @param object Plain object
                 * @returns FrameOutput
                 */
                public static fromObject(object: { [k: string]: any }): bayesmech.vision.PongtownResponse.FrameOutput;

                /**
                 * Creates a plain object from a FrameOutput message. Also converts values to other types if specified.
                 * @param message FrameOutput
                 * @param [options] Conversion options
                 * @returns Plain object
                 */
                public static toObject(message: bayesmech.vision.PongtownResponse.FrameOutput, options?: $protobuf.IConversionOptions): { [k: string]: any };

                /**
                 * Converts this FrameOutput to JSON.
                 * @returns JSON object
                 */
                public toJSON(): { [k: string]: any };

                /**
                 * Gets the default type url for FrameOutput
                 * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
                 * @returns The default type url
                 */
                public static getTypeUrl(typeUrlPrefix?: string): string;
            }

            /** Properties of a GlobalTablePose. */
            interface IGlobalTablePose {

                /** GlobalTablePose hasPose */
                hasPose?: (boolean|null);

                /** GlobalTablePose TTableToWorld */
                TTableToWorld?: (number[]|null);

                /** GlobalTablePose refinedCost */
                refinedCost?: (number|null);

                /** GlobalTablePose framesUsed */
                framesUsed?: (number|null);

                /** GlobalTablePose meanIou */
                meanIou?: (number|null);

                /** GlobalTablePose p10Iou */
                p10Iou?: (number|null);

                /** GlobalTablePose p90Iou */
                p90Iou?: (number|null);

                /** GlobalTablePose hasNetPose */
                hasNetPose?: (boolean|null);

                /** GlobalTablePose TNetToWorld */
                TNetToWorld?: (number[]|null);
            }

            /** Represents a GlobalTablePose. */
            class GlobalTablePose implements IGlobalTablePose {

                /**
                 * Constructs a new GlobalTablePose.
                 * @param [properties] Properties to set
                 */
                constructor(properties?: bayesmech.vision.PongtownResponse.IGlobalTablePose);

                /** GlobalTablePose hasPose. */
                public hasPose: boolean;

                /** GlobalTablePose TTableToWorld. */
                public TTableToWorld: number[];

                /** GlobalTablePose refinedCost. */
                public refinedCost: number;

                /** GlobalTablePose framesUsed. */
                public framesUsed: number;

                /** GlobalTablePose meanIou. */
                public meanIou: number;

                /** GlobalTablePose p10Iou. */
                public p10Iou: number;

                /** GlobalTablePose p90Iou. */
                public p90Iou: number;

                /** GlobalTablePose hasNetPose. */
                public hasNetPose: boolean;

                /** GlobalTablePose TNetToWorld. */
                public TNetToWorld: number[];

                /**
                 * Creates a new GlobalTablePose instance using the specified properties.
                 * @param [properties] Properties to set
                 * @returns GlobalTablePose instance
                 */
                public static create(properties?: bayesmech.vision.PongtownResponse.IGlobalTablePose): bayesmech.vision.PongtownResponse.GlobalTablePose;

                /**
                 * Encodes the specified GlobalTablePose message. Does not implicitly {@link bayesmech.vision.PongtownResponse.GlobalTablePose.verify|verify} messages.
                 * @param message GlobalTablePose message or plain object to encode
                 * @param [writer] Writer to encode to
                 * @returns Writer
                 */
                public static encode(message: bayesmech.vision.PongtownResponse.IGlobalTablePose, writer?: $protobuf.Writer): $protobuf.Writer;

                /**
                 * Encodes the specified GlobalTablePose message, length delimited. Does not implicitly {@link bayesmech.vision.PongtownResponse.GlobalTablePose.verify|verify} messages.
                 * @param message GlobalTablePose message or plain object to encode
                 * @param [writer] Writer to encode to
                 * @returns Writer
                 */
                public static encodeDelimited(message: bayesmech.vision.PongtownResponse.IGlobalTablePose, writer?: $protobuf.Writer): $protobuf.Writer;

                /**
                 * Decodes a GlobalTablePose message from the specified reader or buffer.
                 * @param reader Reader or buffer to decode from
                 * @param [length] Message length if known beforehand
                 * @returns GlobalTablePose
                 * @throws {Error} If the payload is not a reader or valid buffer
                 * @throws {$protobuf.util.ProtocolError} If required fields are missing
                 */
                public static decode(reader: ($protobuf.Reader|Uint8Array), length?: number): bayesmech.vision.PongtownResponse.GlobalTablePose;

                /**
                 * Decodes a GlobalTablePose message from the specified reader or buffer, length delimited.
                 * @param reader Reader or buffer to decode from
                 * @returns GlobalTablePose
                 * @throws {Error} If the payload is not a reader or valid buffer
                 * @throws {$protobuf.util.ProtocolError} If required fields are missing
                 */
                public static decodeDelimited(reader: ($protobuf.Reader|Uint8Array)): bayesmech.vision.PongtownResponse.GlobalTablePose;

                /**
                 * Verifies a GlobalTablePose message.
                 * @param message Plain object to verify
                 * @returns `null` if valid, otherwise the reason why it is not
                 */
                public static verify(message: { [k: string]: any }): (string|null);

                /**
                 * Creates a GlobalTablePose message from a plain object. Also converts values to their respective internal types.
                 * @param object Plain object
                 * @returns GlobalTablePose
                 */
                public static fromObject(object: { [k: string]: any }): bayesmech.vision.PongtownResponse.GlobalTablePose;

                /**
                 * Creates a plain object from a GlobalTablePose message. Also converts values to other types if specified.
                 * @param message GlobalTablePose
                 * @param [options] Conversion options
                 * @returns Plain object
                 */
                public static toObject(message: bayesmech.vision.PongtownResponse.GlobalTablePose, options?: $protobuf.IConversionOptions): { [k: string]: any };

                /**
                 * Converts this GlobalTablePose to JSON.
                 * @returns JSON object
                 */
                public toJSON(): { [k: string]: any };

                /**
                 * Gets the default type url for GlobalTablePose
                 * @param [typeUrlPrefix] your custom typeUrlPrefix(default "type.googleapis.com")
                 * @returns The default type url
                 */
                public static getTypeUrl(typeUrlPrefix?: string): string;
            }
        }
    }
}
