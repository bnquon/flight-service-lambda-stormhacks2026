FROM public.ecr.aws/lambda/python:3.13

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN python -m pip install --no-cache-dir \
    --target ${LAMBDA_TASK_ROOT} \
    -r ${LAMBDA_TASK_ROOT}/requirements.txt

# The browser runs in Skyvern Cloud; no local browser binaries are needed.
COPY lambda_function.py request.py service.py google_flights.py \
    google_flights_navigation.py updates.py storage.py ${LAMBDA_TASK_ROOT}/

CMD ["lambda_function.lambda_handler"]
